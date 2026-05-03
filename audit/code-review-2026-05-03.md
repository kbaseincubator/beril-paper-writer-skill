# Code Audit: beril-paper-writer-skill (2026-05-03)

**Scope:** Python helpers + bash orchestrator + test suite. Excludes prompts (*.v1.md).

**Summary:** The codebase exhibits solid defensive patterns (atomic writes, validation, error recovery) but has several footguns around error handling, state management, and input validation that could bite in production. Below are the findings organized by severity.

---

## CRITICAL

### 1. Null-coalescing bug in `paper_writer_helpers.py` (multiple locations)

**File:** `src/beril_paper_writer/skill/tools/paper_writer_helpers.py`
**Lines:** 301, 304
**Issue:** The `add_cost` and `add_elapsed_seconds` subcommands use `dict.get()` with defaults to initialize accumulators, but `dict.get(key, default)` returns `default` when the key is **missing**, NOT when it's `None`. If state.json contains `"cost_so_far_usd": null`, the code returns `None` instead of `0.0`, causing a TypeError on the float conversion.

```python
# Line 301
state["cost_so_far_usd"] = float(state.get("cost_so_far_usd", 0.0)) + float(args.add_cost)

# This fails if state["cost_so_far_usd"] is null in JSON
```

**Fix:** Use the `(state.get(...) or default)` pattern per memory feedback_dict_get_default_vs_null.md:
```python
state["cost_so_far_usd"] = float((state.get("cost_so_far_usd") or 0.0)) + float(args.add_cost)
state["elapsed_seconds"] = float((state.get("elapsed_seconds") or 0.0)) + float(args.add_elapsed_seconds)
```

**Severity:** Critical because cost/elapsed tracking failures are silent (except as crashes mid-run, breaking the orchestrator's audit trail).

---

### 2. String injection in bash heredoc (paper_writer_helpers.py, line 429-436)

**File:** `src/beril_paper_writer/skill/tools/paper_writer.sh`
**Lines:** 429-436
**Issue:** The inline Python script in `phase_init()` constructs JSON dict keys using bash string interpolation without escaping. If `project_id` contains a quote or backslash, the JSON is malformed:

```bash
d['project_id'] = '$project_id'  # If project_id = foo'bar, JSON syntax error
```

**Fix:** Use JSON encoding for the value:
```bash
"$PYTHON_BIN" -c "
import json, sys
project_id = json.loads('''$("$PYTHON_BIN" -c "import json; print(json.dumps('$project_id'))")''')
with open('$draft_dir/state.json') as f:
    d = json.load(f)
d['project_id'] = project_id
d['mode'] = '$PAPER_WRITER_MODE'
with open('$draft_dir/state.json', 'w') as f:
    json.dump(d, f, indent=2, sort_keys=True)
"
```

Or simpler: pass project_id via a JSON file (per feedback_bash_to_argparse_use_json_files.md):
```bash
python3 -c "import json, sys; json.dump({'project_id': '$project_id', 'mode': '${PAPER_WRITER_MODE:-paper}'}, sys.stdout)" | \
  "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" update-state-from-json "$draft_dir"
```

---

### 3. Silent truncation in `_parse_reframing_log` (paper_writer_helpers.py, line 1310-1411)

**File:** `src/beril_paper_writer/skill/tools/paper_writer_helpers.py`
**Lines:** 1379-1380
**Issue:** Multi-line field values in reframing_log are appended to with a space separator, but the parser silently truncates continuation lines that start mid-paragraph. If a user writes:

```markdown
## Entry 1 — type: reframing
- **Issue:** First line
  continued on next line without a bullet

- **Resolution:** Action taken
```

The continuation line (without a leading `- `) is **silently ignored** (line 1379 checks `if current_field and line.strip()` but field accumulation only happens in the `if current_field` branch). The "continued on next line" text never reaches the entry dictionary.

**Fix:** Explicitly handle multi-line paragraph text:
```python
if current_field and line.strip():
    # Continuation line for current field
    current[current_field] += " " + line.strip()
elif not current_field and line.strip() and not fm and not entry_re.match(line):
    # Continuation line when no field is active — attach to the most recent field
    # OR: emit a warning that this line is orphaned
    if current and current_field:
        current[current_field] += " " + line.strip()
    # else: silently orphaned text (add a warning log)
```

Better: add a validation check that emits a WARN for unparsed lines.

---

### 4. Race condition in lock acquisition (paper_writer_helpers.py, line 504-601)

**File:** `src/beril_paper_writer/skill/tools/paper_writer_helpers.py`
**Lines:** 537-599
**Issue:** The `acquire_lock` subcommand documents a ~1ms race window. If two `paper_writer.sh` processes fire within 1ms:
1. Both read lock_file (doesn't exist) ✓
2. Both pass the "no existing lock" check ✓
3. Both call `_atomic_write_text(lock_file, body)` — **only one succeeds**
4. The loser silently overwrites the winner's lock

Since the script relies on liveness checks (os.kill), both locks are considered "live," and the second writer clobbers the first. The user sees no error; two writers proceed in parallel.

**Fix:** Use `os.open(path, os.O_CREAT | os.O_EXCL)` for atomic lock creation instead of atomic_write:
```python
try:
    fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    os.write(fd, body.encode('utf-8'))
    os.close(fd)
except FileExistsError:
    # Another writer won the race; read their lock and report
    ...
```

Or add a `fstat + compare mtime + retry` to detect when another writer has just acquired it between the "no lock" check and atomic_write.

---

## IMPORTANT

### 5. Uncaught `json.JSONDecodeError` in `emit_next_actions` (paper_writer_helpers.py)

**File:** `src/beril_paper_writer/skill/tools/paper_writer_helpers.py`
**Lines:** 654-661, 684, 703
**Issue:** Multiple file-read sites call `.read_text().splitlines()` and then parse the result as JSON (or search for patterns) without wrapping the JSON.loads in a try-except. If a file is truncated or corrupted:

```python
# Line 656 (in emit_next_actions)
v = json.loads(validation_path.read_text(encoding="utf-8"))
# No except JSONDecodeError — crash if malformed
for entry in v.get("validators", []):  # KeyError if v is not a dict
```

**Fix:** Wrap each JSON parse in a try-except or skip-on-error pattern:
```python
if validation_path.is_file():
    try:
        v = json.loads(validation_path.read_text(encoding="utf-8"))
        if isinstance(v, dict):
            for entry in v.get("validators", []):
                ...
    except (json.JSONDecodeError, OSError) as e:
        lines.append(f"_(validation.json could not be parsed: {e})_")
        lines.append("")
```

---

### 6. Inconsistent error handling in `invoke_claude` (paper_writer.sh, lines 277-337)

**File:** `src/beril_paper_writer/skill/tools/paper_writer.sh`
**Lines:** 309-324 (with pipe to stream_progress.py)
**Issue:** When piping `claude -p` output through `stream_progress.py`, if stream_progress.py crashes (e.g., out of memory, disk full), the bash script exits with stream_progress's exit code, **not** claude's. However, the orchestrator's retry logic in `invoke_claude_with_retry` (line 342+) assumes:
- exit 0 = success
- exit 2 = retryable (Write not invoked)
- exit 3 = wrong path (not retryable)
- other = permanent failure

If stream_progress.py exits 1 (e.g., permission error writing metadata), the orchestrator treats it as "permanent failure" even though the LLM call itself succeeded and wrote the file. The file is then deleted (line 397) and the run is aborted.

**Fix:** Preserve claude's exit code separately from stream_progress.py's. Capture both:
```bash
# Pipe claude into stream_progress and capture both exit codes
"$PYTHON_BIN" "$TOOLS_DIR/stream_progress.py" ... &
parser_pid=$!
rc_claude=$?
wait $parser_pid
rc_parser=$?

# If claude failed, use its exit code; if stream_progress failed, log a warning
if [[ $rc_claude -ne 0 ]]; then
    return $rc_claude
elif [[ $rc_parser -ne 0 ]]; then
    log_warn "stream_progress.py exited $rc_parser; assuming Write succeeded"
    return 0
fi
```

---

### 7. Missing validation in `check_repair_status` (paper_writer_helpers.py, line 1233-1258)

**File:** `src/beril_paper_writer/skill/tools/paper_writer_helpers.py`
**Lines:** 1233-1258
**Issue:** `cmd_check_repair_status` reads validation.json and looks up a validator by ID, but if the entry exists with **status != "fail"**, it still returns that status. The orchestrator's REPAIR_MODE logic assumes this function only returns statuses for validators that are being repaired (and thus should be failing). If a validator passes and you call `check_repair_status` on it, you get "pass" — but the orchestrator may then skip repair and proceed, incorrectly assuming the validator still failed.

The docstring says "post-repair" but there's no check that the validator **was** failing before repair.

**Fix:** Return "unknown" if the validator's status is not "fail":
```python
if entry is None or entry.get('status') != 'fail':
    print("STATUS=unknown")
    print(f"NOTE=validator {args.validator} not in fail status")
    return 0
```

---

### 8. Path traversal in `render_image` (assemble_docx.py, lines 352-368)

**File:** `src/beril_paper_writer/skill/tools/assemble_docx.py`
**Lines:** 352-368
**Issue:** The image-path validation checks for `..` in `Path(path).parts`, but this check is **incomplete**. A path like `figures/../../../etc/passwd` (with forward slashes normalized) would pass the check because `Path(path).parts` normalizes `..` **after** construction. A malicious actor who can write to the markdown source could embed:

```markdown
![Figure 1](figures/../../../etc/passwd)
```

The validation checks `Path("figures/../../../etc/passwd").parts` = `('figures', '..', '..', '..', 'etc', 'passwd')` — which **contains** `..`, so the check should catch it. However, the code then calls:

```python
full_path = (base_dir / path).resolve()
```

The `.resolve()` normalizes the path, so if base_dir is `/home/user/project`, the result is `/etc/passwd` — a true path traversal.

**Fix:** Resolve the path **before** the check:
```python
full_path = (base_dir / path).resolve()
# Ensure full_path is within base_dir
try:
    full_path.relative_to(base_dir)
except ValueError:
    # full_path is outside base_dir (path traversal attempt)
    sys.stderr.write(f"WARN: image path escapes draft dir; rejecting: {path}\n")
    return
```

---

### 9. Silent error in `_write_throughline_verbatim` (continue_run.py, line 171-199)

**File:** `src/beril_paper_writer/commands/continue_run.py`
**Lines:** 171-199
**Issue:** The function extracts the candidate block from throughline_candidates.md by finding the `## Candidate TLN:` header and splitting on it. If the header is malformed (e.g., `## Candidate TL2` without a colon), the function returns an empty `title` string (line 184 checks `if len(after_colon) > 1` but doesn't validate that a colon was found). The resulting throughline file has:

```markdown
# Throughline

**Selected:** TL2 (carried verbatim from plan.v1 candidate; no user revision applied).

**Statement:** 

[blank, since title="""]
```

This silently produces a broken throughline with an empty statement. The pipeline continues with a blank statement, which downstream prompts will have trouble with.

**Fix:** Validate that the header contains a colon and has non-empty content:
```python
if line.startswith("## Candidate "):
    if ":" not in line:
        raise ValueError(f"Malformed candidate header: {line!r}")
    after_colon = line.split(":", 1)
    title = after_colon[1].strip()
    if not title:
        raise ValueError(f"Candidate header has empty title: {line!r}")
    body_start = i + 1
    break
```

---

### 10. Unescaped format string in `cmd_prepare_repair` (paper_writer_helpers.py, line 1200)

**File:** `src/beril_paper_writer/skill/tools/paper_writer_helpers.py`
**Lines:** 1199-1200
**Issue:** When an escalation happens, the code prints:

```python
print(f"ESCALATION_NOTE={dispatch['escalation_note']}")
```

If `dispatch['escalation_note']` contains shell metacharacters (e.g., `$VAR`, backticks), the bash script that sources this output will interpret them. This is less critical than a code injection (since the escalation_note is from code, not user input), but it's still a footgun.

**Fix:** Quote the value in the output, or use proper shell escaping:
```python
import shlex
note = dispatch['escalation_note']
print(f"ESCALATION_NOTE={shlex.quote(note)}")
```

---

## SUGGESTED

### 11. Missing coverage: `extract_figures.py` notebook parsing

**File:** `src/beril_paper_writer/skill/tools/extract_figures.py`
**Issue:** The script has extensive AST parsing for savefig calls but the unit tests (in `tests/unit/test_extract_figures.py`) are sparse. The functions `_last_string_in_path_expr`, `_is_savefig_call`, and the full figure-extraction pipeline are critical for identifying which figures to use, but there are no tests for:
- malformed f-strings in savefig paths
- nested BinOp (e.g., `FIGS / "sub" / "fig.png"`) with multiple `/` operators
- `Path(...)` calls with variable arguments (not constant strings)
- Magics that break the AST (e.g., `%matplotlib inline`)

**Suggested fixes:**
1. Add parametrized tests for edge-case path expressions in savefig calls
2. Test that notebook AST extraction gracefully skips non-constant paths (and logs a note)
3. Test round-trip: notebook → extract → inventory → serialize → deserialize → validate

---

### 12. Magic string in `check_caption_provenance.py` (line 214-224)

**File:** `src/beril_paper_writer/skill/tools/check_caption_provenance.py`
**Lines:** 214-224
**Issue:** The allow-list for named entities is a hardcoded set:

```python
_NAMED_ENTITY_ALLOW = {
    "Each Panel",
    "The Distribution",
    "Same Scale",
    ...
}
```

This list is fragile and grows as new caption patterns are discovered. When a new pattern appears in live captions, the developer must manually add it to the allow-list.

**Suggested fix:** Make this configurable via an external YAML file, or generate the allow-list by examining a corpus of known-good captions (per SPEC §6.3 phrase-mining).

---

### 13. Missing `__init__` in `tools/__init__.py`

**File:** `src/beril_paper_writer/skill/tools/__init__.py`
**Issue:** The file is empty. While not a bug, this prevents importing submodules cleanly (e.g., `from beril_paper_writer.skill.tools import citation_pool`). Most imports work via direct `import beril_paper_writer.skill.tools.citation_pool`, but tests that run from the root would benefit from an explicit `__init__` that re-exports the main modules.

**Suggested fix:** Add:
```python
"""Paper-writer orchestration and validation tools."""

from . import (
    assemble_docx,
    citation_pool,
    check_caption_provenance,
    check_figures_manifest,
    check_overclaim,
    check_repair_scope,
    check_scope_coherence,
    check_tables_manifest,
    check_throughline_glyphs,
    extract_figures,
    extract_methods,
    extract_tables,
    paper_writer_helpers,
    validate_manuscript,
)

__all__ = [
    "assemble_docx",
    "citation_pool",
    "check_caption_provenance",
    ...
]
```

---

### 14. Duplicate strip-magics helper (extract_figures.py vs extract_methods.py)

**File:** `src/beril_paper_writer/skill/tools/extract_figures.py` (line 324-331)
**Issue:** The function `_strip_jupyter_magics` is defined in both `extract_figures.py` and `extract_methods.py` (as noted in the file's own docstring: "vendored from extract_methods.py"). Duplicating a 10-line helper is fine, but if IPython magic syntax ever changes, both copies need updating.

**Suggested fix:** Move to a shared `jupyter_utils.py` module and import:
```python
# jupyter_utils.py
_MAGIC_RE = re.compile(r"^\s*[%!?]")
def strip_jupyter_magics(source: str) -> str:
    ...

# extract_figures.py and extract_methods.py
from beril_paper_writer.skill.tools import jupyter_utils
jupyter_utils.strip_jupyter_magics(source)
```

---

### 15. Mutable default in `cmd_write_handoff` (paper_writer_helpers.py, line 178-189)

**File:** `src/beril_paper_writer/skill/tools/paper_writer_helpers.py`
**Lines:** 178-189
**Issue:** No actual mutable-default bug here (the code doesn't use mutable defaults in the function signature), but the accumulation of choices into a list-of-dicts is done in-place:

```python
choices = []
for entry in args.choice:
    ...
    choices.append({"id": cid.strip(), "label": label.strip()})
payload["choices"] = choices
```

This is fine, but the adjacent code path (lines 170-177) directly assigns a JSON-loaded list without defensive copying. If the JSON list is later mutated, it can mutate the in-memory state. Not critical (the JSON is read once), but good defensive practice would be:

```python
payload["choices"] = [dict(c) for c in choices_data]  # Defensive copy
```

---

### 16. Inconsistent return types in `_parse_reframing_log` (paper_writer_helpers.py, line 1310-1411)

**File:** `src/beril_paper_writer/skill/tools/paper_writer_helpers.py`
**Lines:** 1310-1411
**Issue:** The function returns a list of dicts with string keys, but some keys have inconsistent types:
- `entry_number` is always an int
- `type` is always a str
- But `entry["resolution_action"]` is a string ("escalated" | "accepted" | "unknown")

Downstream code (line 1479) checks `if entry["resolution_action"] != "escalated"` — this is fine, but there's no validation that the returned string is one of the expected values. If parsing fails to extract a word match, the default is "unknown" (line 1396), which is reasonable, but this should be documented more clearly.

**Suggested fix:** Add a `ResolutionAction` enum or validation:
```python
from enum import Enum

class ResolutionAction(str, Enum):
    ESCALATED = "escalated"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"

# Then validate:
entry["resolution_action"] = ResolutionAction(action).value
```

Or add an assertion:
```python
assert entry["resolution_action"] in ("escalated", "accepted", "unknown")
```

---

### 17. No defense against circular references in `diff_artifacts` (state.py, line 143-171)

**File:** `src/beril_paper_writer/state.py`
**Lines:** 143-171
**Issue:** The `diff_artifacts` function is pure and takes two lists of ArtifactHash objects. There's no check that:
1. The lists don't contain duplicate paths
2. The paths are valid relative paths (no `..`, `/`, etc.)

If a path appears twice in the input (a bug upstream), the function silently uses the last one in the set. This could hide data loss.

**Suggested fix:** Validate inputs:
```python
def diff_artifacts(previous, current) -> ArtifactDiff:
    prev_by_path = {a.path: a for a in previous}
    curr_by_path = {a.path: a for a in current}
    
    # Detect duplicates
    if len(prev_by_path) < len(previous):
        dups = [p for p in set(a.path for a in previous) if sum(1 for a in previous if a.path == p) > 1]
        raise ValueError(f"Duplicate paths in previous artifacts: {dups}")
    ...
```

---

### 18. Incomplete test coverage for state transitions

**File:** `tests/unit/test_state.py`
**Issue:** The test suite covers round-tripping state and hashing, but there are no tests for:
1. State transitions across phases (e.g., init → triage → throughline_pick)
2. Detecting when user edits a manuscript file (the `is_user_edited` function)
3. Throughline reevaluation tracking (the `ThroughlineReevaluation` and `ThroughlineState` classes)
4. Analysis-request state mutation

**Suggested fix:** Add integration tests for the full state-machine lifecycle.

---

### 19. `invoke_claude_with_retry` doesn't cap retries on timeout

**File:** `src/beril_paper_writer/skill/tools/paper_writer.sh`
**Lines:** 346-410
**Issue:** The retry loop (line 382-405) has a hardcoded MAX=3, but there's no timeout per attempt. If an LLM call hangs (which can happen with API timeouts), each retry can wait indefinitely, extending the total wall-clock time by 3x.

**Suggested fix:** Add a per-call timeout:
```bash
timeout 5m invoke_claude "$sys_prompt_file" "$prompt" ...
rc=$?
case $rc in
    124) log_warn "Timeout on $label"; rc=2 ;;  # Treat timeout as retryable
esac
```

---

### 20. No validation that LLM output satisfies `expected_write_path`

**File:** `src/beril_paper_writer/skill/tools/stream_progress.py` (indirectly)
**Issue:** The orchestrator passes `--expected-write-path` to stream_progress.py, which waits for the LLM to call Write on that exact path. However, there's no check that:
1. The written file is non-empty
2. The written file is valid for its expected format (e.g., if it's a JSON file, the JSON is valid)

If the LLM writes an empty file or syntactically-invalid output, stream_progress.py exits 0 (success), and the orchestrator proceeds with garbage data.

**Suggested fix:** Add optional post-write validators in stream_progress.py:
```bash
invoke_claude ... | stream_progress.py \
    --expected-write-path "$path" \
    --post-write-validator "json" \  # or "markdown", "python", etc.
```

---

## SUMMARY ASSESSMENT

**Overall Risk Level:** MEDIUM-HIGH

The codebase is well-structured with good separation of concerns (bash orchestrator, Python helpers, validators) and strong atomic-write discipline. However, there are several footguns:

1. **State management** is fragile (null-coalescing bugs, race conditions in lock acquisition)
2. **Error recovery** has gaps (uncaught JSON errors, inconsistent exit-code handling in pipes)
3. **Input validation** is incomplete (path traversal, shell injection, malformed markdown)

**Immediate actions before v0.6.0 shipment:**
- Fix critical #1 (null-coalescing) — causes silent cost/elapsed tracking failures
- Fix critical #2 (string injection) — enables project_id injection
- Fix critical #3 (_parse_reframing_log multi-line) — causes data loss
- Fix critical #4 (lock race) — enables concurrent-write corruption
- Fix important #8 (path traversal) — enables arbitrary file reads
- Fix important #5 (JSON error handling) — causes crashes on malformed files

**Recommend:** Add pre-ship integration tests that exercise state transitions, error recovery, and edge cases in the orchestrator's retry loops.

**Production readiness:** v0.6 is suitable for alpha testing with Adam as the sole user. Before broader release (v1.0), all critical+important findings must be resolved, and test coverage for state/phase transitions should reach >90%.
