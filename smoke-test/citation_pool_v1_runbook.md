# citation_pool.v1 live-LLM smoke test — runbook

**Goal.** Validate that `citation_pool.v1.md`, invoked as a `claude -p`
subagent against a real STRONG-tier BERDL project, produces a usable
`pool.json` that passes schema validation and contains plausibly-
verified citations against a chosen throughline.

**Why this matters.** The orchestrator (Phase 4) doesn't exist yet,
so we can't smoke-test the full drafting flow. But citation_pool.v1
is the prompt with the highest external-API exposure (WebSearch +
maybe PubMed MCP), the strictest output schema, and the most-cited
LLM-paper-writer failure mode (citation hallucination). If this
prompt works end-to-end against a real project, that's strong
evidence the rest of the suite will too. If it fails, the failure
mode tells us which assumptions are wrong before Phase 4 is built
around them.

**Test target.** `functional_dark_matter` at
`/Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter`.
STRONG-tier per Phase 2 smoke testing: REPORT.md has 7 numbered
findings with effect sizes and gap candidates; existing
`references.md` has ~30 entries we can use as seed candidates.

**Cost target.** ~$3–8 per run at `MAX_BUDGET=30` /
`DEPTH=standard` (smaller than the 80-entry default cap; standard
WebSearch budget is ~25–40 calls). Wall clock ~5–15 min.

---

## 0. Pre-flight (5 min)

```bash
cd /Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft

# Confirm the prompt + tool are present
test -f src/beril_paper_writer/skill/prompts/citation_pool.v1.md && echo "prompt OK"
test -f src/beril_paper_writer/skill/tools/citation_pool.py && echo "tool OK"

# Confirm the project we're testing against
PROJECT=/Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter
test -f "$PROJECT/REPORT.md" && echo "REPORT OK"
test -f "$PROJECT/RESEARCH_PLAN.md" && echo "PLAN OK"
test -f "$PROJECT/references.md" && echo "seed references OK"

# Confirm claude CLI is available (required for the smoke test)
which claude && claude --version
```

If any of these fail, halt and surface. Don't proceed with a
half-set-up environment.

---

## 1. Set up the smoke-test working directory (2 min)

We're not using the project's real `papers/` directory — too easy
to leave artifacts behind. Use a sibling test directory:

```bash
SMOKE_DIR=/tmp/citation_pool_smoke
DRAFT_DIR=$SMOKE_DIR/draft_1
mkdir -p "$DRAFT_DIR"

# Confirm we have write access
touch "$DRAFT_DIR/.touch" && rm "$DRAFT_DIR/.touch" && echo "write OK"
```

Copy the manual throughline stub into the draft dir (next step
defines the stub):

```bash
cp /Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft/smoke-test/throughline_stub.md \
   "$DRAFT_DIR/00_throughline.md"
```

---

## 2. Manual throughline construction (the load-bearing input)

Since `plan.v1` hasn't run yet, we hand-craft a single-candidate
throughline file. The stub at `smoke-test/throughline_stub.md`
encodes a plausible STRONG-tier throughline derived from
`REPORT.md`'s actual findings. The agent will use this as its
"the chosen story" anchor.

**Decision point.** Read the stub at
`smoke-test/throughline_stub.md` and decide whether you accept it
as-is. Edit if you want a different story emphasis (the project
genuinely supports several candidate throughlines per the
multi-finding structure of REPORT). Save your edits to
`$DRAFT_DIR/00_throughline.md` (overwriting the copy from step 1).

The stub I drafted picks the cross-organism prioritization
narrative (Findings 4 + 6) as the throughline — the strongest
quantitative claim with the most-defensible scope. Other valid
candidates: the GapMind gap-filling narrative (Finding 3), the
phylogenetic-breadth + experimental-priority narrative (Findings
5 + 7).

---

## 3. Configure inputs and run (5–15 min wall clock)

```bash
# Variables the user prompt template uses
export PROJECT_ROOT=/Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter
export DRAFT_DIR=/tmp/citation_pool_smoke/draft_1
export POOL_JSON_PATH=$DRAFT_DIR/pool.json
export THROUGHLINE_PATH=$DRAFT_DIR/00_throughline.md
export EXISTING_REFERENCES_MD=$PROJECT_ROOT/references.md
export MAX_BUDGET=30
export DEPTH=standard
export MODE=paper
export TIER=STRONG
export VALIDATOR_CMD="python3 /Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft/src/beril_paper_writer/skill/tools/citation_pool.py validate $POOL_JSON_PATH"

# Path to the prompt
SYSTEM_PROMPT=/Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft/src/beril_paper_writer/skill/prompts/citation_pool.v1.md

# Build the user prompt by substituting the variables into the
# template
USER_PROMPT=$(envsubst < /Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft/smoke-test/user_prompt_template.md)

# Sanity-check the substitution worked (no `${...}` left)
echo "$USER_PROMPT" | grep -E '\$[{]?[A-Z_]+' && echo "WARNING: unresolved variables in user prompt"

# Run claude -p with the system prompt and user prompt.
# Output: stream-json so we can parse cost + tool calls afterward.
# Append-system-prompt is the right way to layer; pure -p uses the
# default Claude Code system prompt.
mkdir -p $DRAFT_DIR/audit
LOG_PATH=$DRAFT_DIR/audit/citation_pool.stream.log

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

**Three flag requirements, all confirmed against Claude Code 2.x:**

- **`--verbose`** is required when `--output-format=stream-json` is
  combined with `-p` / `--print`. Without it, Claude Code exits 1
  immediately with `Error: When using --print,
  --output-format=stream-json requires --verbose`.
- **`--permission-mode bypassPermissions`** is required because
  `-p` mode is non-interactive and cannot show permission dialogs.
  Without it, the agent's Read calls into paths outside the cwd
  silently fail with `permission_denials` in the result block, the
  agent halts with an honest "cannot proceed without read access"
  message, and no `pool.json` is written. The prompt's discipline
  IS working in that case (correctly halts rather than fabricating)
  — but the smoke test never gets past input-reading. Alternative
  for production orchestration: explicitly `--add-dir` each needed
  path; that's more surgical but may still prompt for Write
  permission interactively.
- **`--model`** pins the model. If unset, Claude Code uses its
  configured default which may be Opus (~$0.40 per smoke run);
  Sonnet pin lands at ~$0.10. For development iteration of the
  smoke test, pin Sonnet.

If `--append-system-prompt` doesn't work on your version (some
older Claude Code versions used different flag names), try:

```bash
# Alternative: pipe everything and rely on the prompt's role/system
# discipline to scope behavior
cat $SYSTEM_PROMPT > /tmp/full_prompt.md
echo "" >> /tmp/full_prompt.md
echo "---" >> /tmp/full_prompt.md
echo "" >> /tmp/full_prompt.md
echo "$USER_PROMPT" >> /tmp/full_prompt.md
claude -p "$(cat /tmp/full_prompt.md)" \
  --output-format stream-json --verbose \
  > "$LOG_PATH" 2>&1
```

If neither flag form works, run `claude --help` and report which
flags exist — adjust the runbook before retrying.

---

## 4. Validate the output (5–10 min)

```bash
# Did pool.json land?
test -f "$POOL_JSON_PATH" && echo "pool.json written" || echo "FAIL: pool.json missing"

# Schema validation — the pool's own validator
# Note: zsh does NOT word-split unquoted variable expansion by default,
# so `$VALIDATOR_CMD` (containing a multi-word command) is treated as a
# single literal path in zsh and fails with exit 127. Use `eval` or
# invoke the command directly.
eval "$VALIDATOR_CMD"
VAL_EXIT=$?
echo "Validator exit code: $VAL_EXIT (0 = pass, 1 = errors)"

# How many entries?
ENTRY_COUNT=$(python3 -c "import json; d=json.load(open('$POOL_JSON_PATH')); print(len(d['entries']))")
echo "Entries: $ENTRY_COUNT (target 15-30 for MAX_BUDGET=30)"

# Spot-check 3 entries: are they real?
python3 <<'PY'
import json, random
d = json.load(open('/tmp/citation_pool_smoke/draft_1/pool.json'))
sample = random.sample(d['entries'], min(3, len(d['entries'])))
for i, e in enumerate(sample, 1):
    print(f"\n--- Sample entry {i} ---")
    print(f"  Authors: {e.get('authors')}")
    print(f"  Year: {e.get('year')}")
    print(f"  Title: {e.get('title')[:80]}")
    print(f"  Venue: {e.get('venue')}")
    print(f"  DOI: {e.get('doi')}")
    print(f"  PMID: {e.get('pmid')}")
    print(f"  Studied: {e.get('studied')[:80]}")
    print(f"  Finding: {e.get('finding')[:120]}")
    print(f"  Scope: {e.get('scope_alignment')}, Assessment: {e.get('assessment')}")
PY
```

Then **manually spot-check** the 3 sampled entries via Google
Scholar / PubMed lookup. Specifically:

1. Does the DOI / PMID resolve?
2. Does the resolved paper match the title + authors + year?
3. Does the `finding` field accurately summarize what the paper
   actually shows?
4. Does `scope_alignment` honestly reflect how directly the paper
   bears on the throughline's claim?

If 3/3 spot-checks pass, the prompt is functioning. If 1+ fail,
that's a real signal — the prompt's verification discipline isn't
holding up. Document which check failed and how.

---

## 5. Read the closing message + audit log (3 min)

```bash
# The closing message tells us the prompt's self-assessment
tail -50 "$LOG_PATH" | grep -A 5 "pool.json written"

# Token + cost summary from the stream-json log
python3 <<'PY'
import json
import re
with open('/tmp/citation_pool_smoke/draft_1/audit/citation_pool.stream.log') as f:
    for line in f:
        line = line.strip()
        if not line or not line.startswith('{'):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Stream-json events with cost / token info
        if obj.get('type') == 'result':
            print(f"Result: cost ${obj.get('total_cost_usd', 'unknown')}, "
                  f"in_tokens {obj.get('usage', {}).get('input_tokens', 'unknown')}, "
                  f"out_tokens {obj.get('usage', {}).get('output_tokens', 'unknown')}")
        if obj.get('type') == 'assistant' and 'message' in obj:
            msg = obj['message']
            for block in msg.get('content', []):
                if block.get('type') == 'tool_use':
                    print(f"Tool: {block.get('name')}")
PY
```

Expected closing message format (per the prompt's required exact
template):

```
pool.json written, N entries (cap 30, mode standard, PubMed MCP
{available|fallback-WebSearch}); categories covered: [Background,
Methods, Comparators, ...]; uncovered: [...]; WebSearches used: K.
Next: orchestrator must invoke `citation_pool.py format` to render
references.md / bibliography.bib / citation_map.md before
discussion.v1 runs.
```

If the closing message deviates substantially from this template,
that's a finding — the prompt's instruction-following on the
required-exact-format rule didn't hold.

Run the formatter step (the prompt told us this is the orchestrator's
next step) to confirm the pool round-trips through serialization:

```bash
python3 /Users/aparkin/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft/src/beril_paper_writer/skill/tools/citation_pool.py format $POOL_JSON_PATH $DRAFT_DIR

# Should produce references.md, bibliography.bib, citation_map.md
ls $DRAFT_DIR/{references.md,bibliography.bib,citation_map.md}
```

---

## 6. Pass/fail criteria

The smoke test **passes** if:

1. `pool.json` is written.
2. Schema validator returns exit 0 (no errors).
3. Entry count is in the 15–30 range (per MAX_BUDGET).
4. 3/3 sampled entries spot-check correctly (DOI resolves; metadata
   matches; finding accurate; scope honest).
5. Closing message matches the required-exact-format template.
6. Formatter step round-trips cleanly to references.md / bib /
   citation_map.

The smoke test **partially passes** if any one of (1)–(6) fails
but the failure is isolated and doesn't suggest a systemic problem.
Examples:
- 1 spot-check fails but 2/3 pass — possibly a single hallucinated
  citation slipped through; verification discipline mostly works
  but isn't perfect. Worth a follow-up tightening of the
  "high-stakes citation" definition.
- Closing message has minor formatting drift but content is right —
  the template-exact-format rule needs reinforcement; not blocking.

The smoke test **fails** if:
- Validator exits with errors → schema discipline isn't holding.
  Read the validator output verbatim and identify the failing field.
- Entry count is 0 or outside the range → the pool was either
  empty or padded (both bad).
- 2+ spot-checks fail → fabrication / unverified citations
  appearing in the pool. The most-feared failure mode of the entire
  suite.
- Run never produced `pool.json` → tool-call discipline broke.
  Likely a `Write` invocation issue; check the audit log for
  `tool_use` events.

---

## 7. If the test passes

The suite is validated end-to-end on the highest-risk prompt.
Phase 4 (orchestrator) implementation can begin against the LAYOUT
runtime contracts. The smoke-test scaffolding (`runbook.md`,
`throughline_stub.md`, `user_prompt_template.md`) lives in
`smoke-test/` for reuse — commit it.

## 8. If the test fails

Document the failure mode in
`smoke-test/citation_pool_v1_smoke_findings.md` with:

- Which pass/fail criterion failed
- The specific evidence (validator output, spot-check verdict, log
  excerpt)
- A diagnosis: prompt issue? tool issue? environment issue? input
  issue?
- A proposed fix (which prompt section to tighten, OR a runbook
  change, OR a tool fix)

Apply the fix, re-run from step 3. Cap iterations at 3; if still
failing, the prompt's design has an issue worth a deeper review
(possibly another memoryless-reader pass with the failing-run
findings as input).

---

## Appendix: claude CLI flag reference

Different Claude Code versions have used different flags:

- Newer versions: `--append-system-prompt "$(cat ...)"` adds to the
  default system prompt without replacing it.
- Older versions: `--system-prompt "..."` replaces the default
  entirely.

Run `claude --help | grep -i prompt` if uncertain. The smoke test
works under either flag — what matters is that the citation_pool
prompt's content reaches the model as a system-level instruction.

`--output-format stream-json` writes one JSON object per line to
stdout, including tool-use events, partial messages, and the final
result block with cost. This is what audit/cost extraction parses.

If `claude` is invoked without a `model` flag, it picks Claude
Code's configured default. For this smoke test, Sonnet-class is
fine; Opus would be overkill for the budget. Set `--model claude-sonnet-4-...`
if you want to pin.
