# plan.v1 live-LLM smoke test — runbook

**Goal.** Validate that `plan.v1.md`, invoked as a `claude -p`
subagent against the same `functional_dark_matter` project used in
the citation_pool.v1 and methods.v1 smoke tests, produces a usable
`throughline_candidates.md` with rubric-driven triage and
coherent 2–3 candidates.

**Why.** plan.v1 is the **pipeline entry point** — when the
orchestrator runs end-to-end, plan.v1 fires first, produces
candidates, pauses for user pick. If plan.v1 fails or produces
unusable output, the whole pipeline fails. Smoke-testing it
standalone derisks the orchestrator's first step.

Different discipline class than the prior two smoke tests:
- citation_pool.v1: literature verification via WebSearch / PubMed
- methods.v1: notebook AST grounding via `methods_provenance.md`
- **plan.v1: triage + candidate extraction from project artifacts**

Plan.v1 doesn't use WebSearch (no literature pull), doesn't use the
provenance file (it consumes notebooks directly via Read for
evidence-map sourcing), produces a structured markdown artifact
with a strict per-candidate template (per SPEC §4.2).

**Test target.** `functional_dark_matter` (same project).
Expected tier: STRONG (REPORT has 14 numbered findings with effect
sizes / FDR-corrected p-values / explicit Limitations section;
Methods reproducible from notebooks + RESEARCH_PLAN; matches the
STRONG rubric per SPEC §3.1).

**Cost target.** ~$0.30–0.80 on Sonnet, 5–10 min wall clock. No
WebSearch budget; tool calls dominated by Read (REPORT, plan,
several notebooks for sub-claim grounding) plus 1 Write.

---

## 0. Pre-flight (2 min)

```bash
cd /Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft

# Confirm prompt + project + working dir
SMOKE_DIR=/tmp/plan_smoke
DRAFT_DIR=$SMOKE_DIR/draft_1
mkdir -p "$DRAFT_DIR"

PROJECT=/Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter
test -f src/beril_paper_writer/skill/prompts/plan.v1.md && echo "prompt OK"
test -f "$PROJECT/REPORT.md" && echo "REPORT OK"
test -f "$PROJECT/RESEARCH_PLAN.md" && echo "PLAN OK"
test -d "$PROJECT/notebooks" && echo "notebooks OK"

# Initialize empty analysis_requests (orchestrator's job in
# production; we do it manually for the smoke test)
touch "$DRAFT_DIR/analysis_requests.md"
```

No pre-flight extractor needed (plan.v1 doesn't consume
`methods_provenance.md` — it reads notebooks directly when it
needs sub-claim grounding).

---

## 1. Configure inputs and build the user prompt (2 min)

```bash
# Inputs to plan.v1
export PROJECT_ROOT=/Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter
export DRAFT_DIR=/tmp/plan_smoke/draft_1
export THROUGHLINE_CANDIDATES_PATH=$DRAFT_DIR/throughline_candidates.md

SYSTEM_PROMPT=/Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft/src/beril_paper_writer/skill/prompts/plan.v1.md

USER_PROMPT=$(envsubst < /Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft/smoke-test/plan_user_prompt_template.md)

echo "$USER_PROMPT" | grep -E '\$[{]?[A-Z_]+' && echo "WARNING: unresolved variables"
```

---

## 2. Run claude -p (5–10 min wall clock)

```bash
mkdir -p $DRAFT_DIR/audit
LOG_PATH=$DRAFT_DIR/audit/plan.stream.log

claude -p "$USER_PROMPT" \
  --append-system-prompt "$(cat $SYSTEM_PROMPT)" \
  --output-format stream-json \
  --verbose \
  --permission-mode bypassPermissions \
  --model claude-sonnet-4-5-20250929 \
  > "$LOG_PATH" 2>&1
RUN_EXIT=$?
echo "claude -p exit code: $RUN_EXIT"
```

Same flag set as prior smoke tests. Expected wall clock: 5–10 min;
shorter than methods.v1 because plan does less Reading (no
provenance-file consumption; selective notebook reads only when
sub-claims need grounding).

---

## 3. Validate the output (5–10 min)

```bash
# Did throughline_candidates.md land?
test -f "$THROUGHLINE_CANDIDATES_PATH" && \
  echo "throughline_candidates.md written ($(wc -l < $THROUGHLINE_CANDIDATES_PATH) lines, $(wc -w < $THROUGHLINE_CANDIDATES_PATH) words)" \
  || echo "FAIL: throughline_candidates.md missing"

# How many candidates?
echo
echo "=== Candidate headers ==="
grep -nE '^## Candidate ' $THROUGHLINE_CANDIDATES_PATH

# Strength glyphs used?
echo
echo "=== Strength glyph distribution across all evidence maps ==="
grep -oE '✓ direct|⚠ partial|✗ contradicts|◇ orthogonal' $THROUGHLINE_CANDIDATES_PATH | sort | uniq -c

# Per-candidate sections present?
echo
echo "=== Section presence per candidate ==="
echo "Evidence maps:"
grep -c '^\*\*Evidence map:\*\*' $THROUGHLINE_CANDIDATES_PATH
echo "Weakness inventories:"
grep -c '^\*\*Weakness inventory:\*\*' $THROUGHLINE_CANDIDATES_PATH
echo "Would NOT include lists:"
grep -c '^\*\*What this paper would NOT include if this is chosen:\*\*' $THROUGHLINE_CANDIDATES_PATH
```

Then **manually inspect** the file:

```bash
cat $THROUGHLINE_CANDIDATES_PATH
```

What to look for:

1. **Tier verdict** — should be at the top or in the closing
   message; should name STRONG (expected), with specific rationale
   ("REPORT has 14 numbered findings with effect sizes...").
2. **2-3 candidates** (since we expect STRONG, not THIN — no
   narrowed-claim candidate needed).
3. **Each candidate has:**
   - A one-sentence claim (`## Candidate TL{N}: {claim}`).
   - An evidence map table with sub-claims, sources (specific
     notebook+cell or REPORT §), and strength glyphs.
   - A weakness inventory (project-specific, not generic).
   - A "what this paper would NOT include" list with project-
     specific findings.
4. **Strength glyphs operationalized.** Walk a sample of
   `✓ direct` entries and verify the source quantitatively
   establishes the sub-claim. If everything is `✓ direct`, that's
   strength inflation.
5. **Contradicting evidence visible.** If REPORT has any
   findings that contradict any candidate, those should appear in
   that candidate's evidence map as `✗ contradicts`. Hidden
   contradictions = strength inflation.
6. **No causal language in claims.** STRONG-tier claims are
   declarative-with-scope; not "X causes Y" but "X correlates
   with Y across our 48-organism cohort."

---

## 4. Closing message + cost (3 min)

```bash
python3 <<'PY'
import json
last_text = None
last_obj = None
with open('/tmp/plan_smoke/draft_1/audit/plan.stream.log') as f:
    for line in f:
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get('type') == 'assistant':
            for block in obj.get('message', {}).get('content', []):
                if block.get('type') == 'text':
                    last_text = block.get('text')
        if obj.get('type') == 'result':
            last_obj = obj

print("=== LAST ASSISTANT TEXT (closing message) ===")
print(last_text or '(none)')
print()
print("=== RESULT BLOCK summary ===")
if last_obj:
    print(f"Cost: ${last_obj.get('total_cost_usd')}")
    print(f"Duration: {last_obj.get('duration_ms', 0)/1000:.1f}s")
    print(f"Turns: {last_obj.get('num_turns')}")
PY

# Tool-call profile
python3 <<'PY'
import json
from collections import Counter
events = []
with open('/tmp/plan_smoke/draft_1/audit/plan.stream.log') as f:
    for line in f:
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get('type') == 'assistant':
            for block in obj.get('message', {}).get('content', []):
                if block.get('type') == 'tool_use':
                    events.append(block.get('name'))
print(Counter(events))
PY
```

Expected closing-message format (drafting mode):

```
throughline_candidates.md written, {N} candidates (tier: {STRONG|THIN|EXPLORATORY},
recommended mode: {paper|report}); triage rationale: <one-sentence
why-this-tier>. Pause for user pick — invoke
`beril-paper-writer continue {DRAFT_DIR}` after editing
candidates if needed.
```

Expected tool-call profile: dominated by `Read` (REPORT, plan,
selectively notebooks for sub-claim grounding); 1 `Write` for
candidates file; possibly 1–2 `Grep` calls if the agent searches
notebooks for specific evidence; **NO `WebSearch`** (the prompt
forbids it).

---

## 5. Pass/fail criteria

The smoke test **passes** if:

1. `throughline_candidates.md` is written.
2. Tier verdict is **STRONG** (expected); rationale names specific
   evidence-strength criteria the project meets.
3. **2-3 candidates** present (since STRONG, not the +1 narrowed
   THIN candidate).
4. Each candidate has all 3 required sections (evidence map +
   weakness inventory + would-NOT-include).
5. Every evidence-map sub-claim has a specific source pointer
   (notebook+cell or REPORT §, not "the notebooks show...").
6. Strength glyph distribution is mixed — not all `✓ direct`.
   At least some `⚠ partial` and ideally at least one
   `✗ contradicts` (REPORT does have honest caveats per the project's
   "Coverage" + "Interpretation" sections; a good candidate set
   surfaces them).
7. Closing-message template followed (drafting mode); names tier
   + recommended mode + triage rationale.

The smoke test **partially passes** if (1)–(7) mostly hold but:

- Tier verdict is correct but rationale is generic ("sample size is
  large" without naming specific criteria) → minor; flag for
  prompt-revision tightening.
- Strength inflation visible (everything `✓ direct`) → moderate;
  the rubric-driven discipline isn't holding under load.
- Single-candidate output — only 1 candidate produced when 2-3
  expected → significant; the prompt's "user picks; you produce
  options" rule didn't hold.

The smoke test **fails** if:

- 4+ candidates produced (not the THIN +1 narrowed; just over-
  production).
- Causal language in claims ("X causes Y").
- Hidden contradictions: REPORT has findings the candidates'
  evidence maps don't surface as `✗ contradicts`.
- Triage-by-vibes (tier verdict given without specific rationale
  pointing at REPORT's content).

---

## 6. If the test passes

Three of the highest-risk prompts validated standalone end-to-end
(citation_pool, methods, plan). Phase 4 (orchestrator MVP)
implementation can begin. Document findings in
`smoke-test/plan_v1_smoke_findings.md` (parallel to
`citation_pool_v1_smoke_findings.md`).

## 7. If the test fails

Per the prior runbooks: document specific failure mode + diagnosis
+ proposed fix. Apply, re-run from §2. Cap iterations at 3.

---

## Appendix: differences from prior smoke tests

| Aspect | citation_pool.v1 | methods.v1 | plan.v1 |
|---|---|---|---|
| Pre-flight extractor | none | `extract_methods.py` | none (reads notebooks via Read) |
| Primary tool | WebSearch | Read | Read |
| WebSearch budget | 25–40 calls | 0 | 0 |
| Cost (Sonnet) | ~$1.50 | ~$0.50–1.50 | ~$0.30–0.80 |
| Wall clock | 8–15 min | 5–10 min | 5–10 min |
| Output format | JSON (strict schema) | markdown (subsection structure) | markdown (strict per-candidate template) |
| Schema validator | `citation_pool.py validate` | none | none in fresh run |
| Pause-and-resume | no | no | **yes** (user picks candidate) |

The pause-and-resume is plan.v1's distinctive feature. The smoke
test ends after the candidates are written; in production, the user
would review them and invoke `beril-paper-writer continue
<draft_dir>` to proceed with drafting. We don't exercise the
continue path in this smoke test.
