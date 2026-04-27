# methods.v1 live-LLM smoke test — runbook

**Goal.** Validate that `methods.v1.md`, invoked as a `claude -p`
subagent against the same `functional_dark_matter` project used in
the citation_pool.v1 smoke test, produces a usable `01_methods.md`
that grounds every claim in the AST-extracted `methods_provenance.md`
and inserts the AI_DISCLOSURE_TEMPLATE verbatim.

**Why.** Different discipline class than `citation_pool.v1`:
- citation_pool.v1: literature verification via WebSearch / PubMed
- methods.v1: notebook AST grounding via `methods_provenance.md`

If both prompts work end-to-end, two of the three highest-risk
prompts in the suite (citation_pool, methods, discussion) are
validated against real data. Phase 4 orchestrator implementation
proceeds with much higher confidence.

**Test target.** `functional_dark_matter` (same project as
citation_pool smoke). 14 notebooks, 271 code cells, 6 unique
statistical tests, statsmodels FDR-BH correction in 3 notebooks,
9 packages with versions captured.

**Cost target.** ~$0.50–1.50 on Sonnet, ~$2–4 on Opus. No WebSearch,
no PubMed-MCP probe — just AST consumption + prose generation.
Wall clock 5–10 min.

---

## 0. Pre-flight: extract methods_provenance.md (3–5 min, $0)

`methods.v1` requires `methods_provenance.md` as its load-bearing
input. Run `extract_methods.py` against the project first.

```bash
cd /Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft

# Where the smoke test will run
SMOKE_DIR=/tmp/methods_smoke
DRAFT_DIR=$SMOKE_DIR/draft_1
mkdir -p "$DRAFT_DIR"

# The project under test (same as citation_pool smoke)
PROJECT=/Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter

# Run the extractor — produces methods_provenance.md in DRAFT_DIR
PYTHONPATH=src python3 -m beril_paper_writer.skill.tools.extract_methods \
  "$PROJECT" --output-dir "$DRAFT_DIR"

# Confirm the output
test -f "$DRAFT_DIR/methods_provenance.md" && \
  echo "methods_provenance.md ready ($(wc -l < $DRAFT_DIR/methods_provenance.md) lines)"
```

Expected: `methods_provenance.md` should be ~50KB / ~800–1000 lines
with section headers `Design Intent`, `Statistical Tests Detected`,
`Software and Versions`, `Imports by Notebook`, `Spark / K-BERDL
Queries`, `Parameters and Thresholds`, `Summary`. The Phase 3
review verified these section names match what `methods.v1`
expects.

If the extractor produces no output or wildly different sections,
halt — methods.v1 will fail without the provenance file.

---

## 1. Reuse the throughline + set up other inputs (2 min)

```bash
# Reuse the throughline from the citation_pool smoke test (same
# project, same chosen throughline). If /tmp/citation_pool_smoke
# was cleaned up, copy the stub from the repo.
if [ -f /tmp/citation_pool_smoke/draft_1/00_throughline.md ]; then
  cp /tmp/citation_pool_smoke/draft_1/00_throughline.md \
     "$DRAFT_DIR/00_throughline.md"
  echo "throughline reused from citation_pool smoke"
else
  cp /Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft/smoke-test/throughline_stub.md \
     "$DRAFT_DIR/00_throughline.md"
  echo "throughline copied from stub (citation_pool smoke output gone)"
fi

# Initialize an empty reframing_log.md (orchestrator's job in
# production; we do it manually for the smoke test)
echo "# Reframing Log" > "$DRAFT_DIR/reframing_log.md"
echo "" >> "$DRAFT_DIR/reframing_log.md"
```

---

## 2. Configure variables and build the user prompt (3 min)

```bash
# Inputs to methods.v1
export PROJECT_ROOT=/Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter
export DRAFT_DIR=/tmp/methods_smoke/draft_1
export METHODS_PATH=$DRAFT_DIR/01_methods.md
export METHODS_PROVENANCE_PATH=$DRAFT_DIR/methods_provenance.md
export RESEARCH_PLAN_PATH=$PROJECT_ROOT/RESEARCH_PLAN.md
export REPORT_PATH=$PROJECT_ROOT/REPORT.md
export THROUGHLINE_PATH=$DRAFT_DIR/00_throughline.md
export REFRAMING_LOG_PATH=$DRAFT_DIR/reframing_log.md
export MODE=paper
export TIER=STRONG

# AI_DISCLOSURE_TEMPLATE: read from the smoke-test stub (not from
# the runtime template; orchestrator template loader doesn't exist
# yet)
export AI_DISCLOSURE_BODY=$(cat /Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft/smoke-test/ai_disclosure_template_stub.md)

# Path to the prompt
SYSTEM_PROMPT=/Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft/src/beril_paper_writer/skill/prompts/methods.v1.md

# Build the user prompt
USER_PROMPT=$(envsubst < /Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft/smoke-test/methods_user_prompt_template.md)

# Sanity-check substitution
echo "$USER_PROMPT" | grep -E '\$[{]?[A-Z_]+' && echo "WARNING: unresolved variables"
```

---

## 3. Run claude -p (5–10 min wall clock)

```bash
mkdir -p $DRAFT_DIR/audit
LOG_PATH=$DRAFT_DIR/audit/methods.stream.log

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

Same flag set as the citation_pool runbook (lessons baked in):
- `--verbose` required with `--output-format=stream-json` + `-p`
- `--permission-mode bypassPermissions` for non-interactive `-p`
- `--model` pinned to Sonnet for cost; drop the flag to use Claude
  Code's default (likely Opus).

Expected wall-clock 5–10 min. Far less than citation_pool's 8 min
because there's no WebSearch verification — just Read + Write +
maybe Bash for the validator self-check (if the prompt runs one;
it doesn't in normal drafting mode per the no-per-section-validator
rule).

---

## 4. Validate the output (5–10 min)

```bash
# Did 01_methods.md land?
test -f "$METHODS_PATH" && echo "01_methods.md written ($(wc -l < $METHODS_PATH) lines, $(wc -w < $METHODS_PATH) words)" \
  || echo "FAIL: 01_methods.md missing"

# Subsections present? Every paper-mode methods should have all 6
# required + optional QC
echo
echo "=== Subsections found ==="
grep -E '^### ' $METHODS_PATH

# AI-Assisted Analysis subsection — should contain the disclosure
# verbatim
echo
echo "=== AI-Assisted Analysis subsection ==="
awk '/^### AI-Assisted Analysis/,/^### /' $METHODS_PATH | head -20

# Did the prompt cite specific tests?
echo
echo "=== Library calls referenced (M5-relevant) ==="
grep -oE '`scipy\.[^`]+`|`statsmodels\.[^`]+`|`numpy\.[^`]+`|`pandas\.[^`]+`' $METHODS_PATH | sort -u

# Multi-test correction declared (M6 awareness)?
echo
echo "=== Multiple-testing correction language ==="
grep -niE 'fdr|benjamini|bonferroni|multipletests|multiple.testing' $METHODS_PATH

# Placeholders the prompt may have left for human follow-up
echo
echo "=== Placeholders ==="
grep -nE '\[METHOD UNCLEAR|\[VERSION UNCLEAR|\[METHOD SOURCE NOT EXTRACTED|\[NEEDS CITATION|\[AI-DISCLOSURE' $METHODS_PATH

# Reframing-log entries (plan-vs-execution discrepancies)
echo
echo "=== Reframing-log entries appended ==="
grep -cE '^## Entry [0-9]+' $REFRAMING_LOG_PATH
```

---

## 5. Closing message + cost (3 min)

```bash
python3 <<'PY'
import json
last_text = None
last_obj = None
with open('/tmp/methods_smoke/draft_1/audit/methods.stream.log') as f:
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
    u = last_obj.get('usage', {})
    print(f"Input tokens: {u.get('input_tokens')}")
    print(f"Cache read tokens: {u.get('cache_read_input_tokens')}")
    print(f"Output tokens: {u.get('output_tokens')}")
PY

# Tool-call profile (lessons from citation_pool: count tool_use
# from stream-json, NOT server_tool_use counters)
python3 <<'PY'
import json
from collections import Counter
events = []
with open('/tmp/methods_smoke/draft_1/audit/methods.stream.log') as f:
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

Expected closing-message format (per the prompt's required exact
template):

```
01_methods.md written, N words; subsections: [<list of subsection
names actually present>]; placeholders: [METHOD UNCLEAR ×K, METHOD
SOURCE NOT EXTRACTED ×L, VERSION UNCLEAR ×M, NEEDS CITATION ×P];
reframing-log entries appended: Q.
```

Like the citation_pool smoke test, the agent may preface the
template line with a self-review summary — that's acceptable
discipline drift, not a failure, as long as the template line
appears verbatim somewhere in the closing message.

Expected tool-call profile: mostly `Read` (4–6 calls: provenance,
plan, throughline, REPORT, possibly schema reference), 1 `Write`
(the methods.md), maybe 1 `Edit` (if the agent revises mid-pass),
and 1 `Read`/`Write` against `reframing_log.md` if any plan-vs-
execution discrepancies were found. NO `WebSearch` calls (the
prompt explicitly forbids them: "Methods prose comes from project
artifacts, not literature").

---

## 6. Pass/fail criteria

The smoke test **passes** if:

1. `01_methods.md` is written.
2. All required ICMJE subsections present: Datasets, Analytical
   Workflow, Statistical Analysis, Software and Versions,
   Computational Environment, AI-Assisted Analysis. (Optional QC
   subsection if the project has explicit threshold gates — the
   provenance file's "Parameters and Thresholds" section had only
   non-literal entries last we checked, so QC subsection should
   be omitted, not stub-stubbed.)
3. AI-Assisted Analysis subsection contains the
   `AI_DISCLOSURE_TEMPLATE` verbatim (exact match — the prompt
   was instructed not to paraphrase).
4. Statistical Analysis subsection cites at least 3 of the 6
   library calls from the provenance file (`scipy.stats.fisher_exact`,
   `scipy.stats.mannwhitneyu`, `scipy.stats.spearmanr`,
   `scipy.stats.chi2_contingency`, `scipy.stats.ks_2samp`,
   `statsmodels.stats.multitest.multipletests`) with software +
   version where the provenance file has them.
5. Multi-test correction declared (the project used
   `method='fdr_bh'` per the provenance file; M6 awareness should
   surface this in the Statistical Analysis subsection).
6. **No fabricated methods.** Spot-check: pick 3 substantive
   Methods sentences and verify they trace to either the provenance
   file's "Statistical Tests" / "Software" / "Imports" / "Spark
   Queries" sections OR a RESEARCH_PLAN section. No "implied" steps
   that don't appear anywhere.
7. Closing-message template line is present (with optional preamble
   self-review summary, like citation_pool).

The smoke test **partially passes** if any one of (1)–(7) fails
in isolation but the failure is bounded:

- 1 fabricated method-claim spotted but 2/3 spot-checks pass — note
  in findings; tighten the prompt's grounding rule if pattern
  repeats.
- AI disclosure paraphrased rather than inserted verbatim — minor
  discipline failure; flag for prompt-revision tightening.
- Closing-message template doesn't appear at all — significant
  format discipline failure.

The smoke test **fails** if:

- 2+ fabricated method-claims spotted — the grounding discipline
  isn't holding. The most-feared failure mode for this prompt.
- Required subsections missing (M-tier validators would fail
  downstream).
- AI-Assisted Analysis subsection missing entirely.
- 0 library calls cited (the prompt didn't actually consume the
  provenance file).

---

## 7. If the test passes

Two of the three highest-risk prompts validated end-to-end.
Document findings in
`smoke-test/methods_v1_smoke_findings.md` (parallel to
`citation_pool_v1_smoke_findings.md`). Phase 4 orchestrator
implementation can proceed.

## 8. If the test fails

Per the citation_pool smoke template: document specific failure
mode + diagnosis + proposed fix. Apply, re-run from step 3. Cap
iterations at 3.

---

## Appendix: differences from citation_pool.v1 smoke test

| Aspect | citation_pool.v1 | methods.v1 |
|---|---|---|
| Pre-flight extractor | none required | `extract_methods.py` must run first |
| Primary tool | WebSearch (verification) | Read (provenance consumption) |
| Cost (Sonnet) | ~$1.50 | ~$0.50–1.00 |
| Cost (Opus) | ~$5.50 | ~$2–4 |
| Wall clock | 8–15 min | 5–10 min |
| Schema validator | `citation_pool.py validate` (pool-specific) | none in fresh run (manuscript-level only) |
| Output format | JSON (strict schema) | markdown (subsection structure) |
| Closing message check | template + format conformance | template only |
| Reframing-log writes | rare (only on pool-exhaustion) | possible (plan-vs-execution discrepancies) |

The methods.v1 smoke test is substantially cheaper and faster —
useful for fast iteration if findings surface and the prompt needs
fixes.
