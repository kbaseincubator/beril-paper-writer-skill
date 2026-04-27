#!/bin/bash
# paper_writer.sh — phase-dispatched orchestrator for beril-paper-writer
#
# Usage:
#   paper_writer.sh draft <project_id_or_path>
#       Initialize a new draft directory under projects/<id>/papers/draft_N/
#       and run preflight + extract + plan.v1. Pauses at the throughline-
#       pick gate; emits .handoff.json and exits 0.
#
#   paper_writer.sh resume <draft_dir>
#       Read state.json's phase and resume from there. Idempotent: each
#       phase checks if its output already exists and skips if so. Pauses
#       at the next gate (citation-pool exhaustion is pump-through in
#       MVP; review pause emits a .handoff.json after the adversarial
#       reviewer runs).
#
# Entry points the Python CLI uses:
#   - cli.py's `draft` subcommand → paper_writer.sh draft <project>
#   - cli.py's `continue` subcommand → after writing 00_throughline.md
#     based on the user's --pick and --revision flags, sets state.phase
#     to "drafting" and invokes paper_writer.sh resume <draft_dir>
#
# This script does NOT handle the user's --pick/--revision args directly;
# that is `continue_run.py`'s job (so the LLM-driven revise step can use
# Python's package imports). The handoff between cli.py and this script
# is purely through state.json's phase field + the on-disk artifacts.
#
# v0.1 MVP scope (per augmentation-stream-plan.md §7):
#   - Linear pipeline; no REPAIR_MODE
#   - Three pause points implemented: throughline_pick (always), review
#     (always at end). Citation-pool exhaustion pumps through with
#     scope-down default (option B2 from the MVP-scope discussion).
#   - Single-pass adversarial review at end (no rewrite loop)
#   - No `assemble` step (markdown→docx); manuscript.md = concatenated
#     section files
#
# Bash 3.2 compatible (macOS default; no associative arrays / ${var^^}).

set -uo pipefail

# ==============================================================================
# Defaults
# ==============================================================================

# Pin Sonnet for cost discipline; opus is overrideable via --model.
DEFAULT_MODEL="claude-sonnet-4-5-20250929"

# Tool grant for claude -p — every prompt should have access to these.
# Bash, WebSearch, and Agent are required by some prompts (citation_pool
# does WebSearch verification; reframer reads multiple files via Read).
CLAUDE_TOOLS="Read,Write,Edit,Bash,Grep,Glob,WebSearch,Agent"

# Default depth (per LAYOUT line 156): standard ~15-25 min per call.
DEFAULT_DEPTH="standard"

# MVP forces these flags to documented values; v0.2 makes them user-
# controllable via slash-command args.
NO_ELICIT=1
NO_ADVERSARIAL_REWRITE=1   # MVP: single-pass review, no rewrite

# ==============================================================================
# Usage
# ==============================================================================

usage() {
    cat <<'EOF'
paper_writer.sh — phase-dispatched orchestrator for beril-paper-writer

Usage:
    paper_writer.sh draft <project_path>           Initialize and run plan.v1; pause for pick.
    paper_writer.sh resume <draft_dir>             Resume from state.json's current phase.

Subcommand-agnostic options (env vars or flags):
    --model <id>       Override default model (env: PAPER_WRITER_MODEL)
    --depth <level>    quick | standard | deep (env: PAPER_WRITER_DEPTH; default: standard)
    --mode <m>         paper | report (env: PAPER_WRITER_MODE; default: tier-driven)
    --no-stream        Disable stream_progress.py (no Write verification)
    --no-adversarial   Skip adversarial reviewer; use fallback prompt
    --help

State file: <draft_dir>/state.json
Handoff file: <draft_dir>/.handoff.json (read by slash-command markdowns)

Exit codes:
    0  success or paused-cleanly-at-handoff
    1  user error (bad args, missing required file)
    2  runtime error (a subprocess failed)
    3  config error (claude not on PATH)
EOF
}

# ==============================================================================
# Logging
# ==============================================================================

log_phase() { echo "" >&2; echo "═══ $* ═══" >&2; }
log_step()  { echo "▸ $*" >&2; }
log_warn()  { echo "⚠ $*" >&2; }
log_error() { echo "❌ $*" >&2; }
log_ok()    { echo "✓ $*" >&2; }

# ==============================================================================
# Path discovery
# ==============================================================================

# Resolve the script's install directory (symlink-safe).
discover_skill_dir() {
    local source="${BASH_SOURCE[0]}"
    while [[ -L "$source" ]]; do
        local dir
        dir="$(cd -P "$(dirname "$source")" && pwd)"
        source="$(readlink "$source")"
        [[ "$source" != /* ]] && source="$dir/$source"
    done
    cd -P "$(dirname "$source")" && pwd
}

# SKILL_DIR points at <BERIL_ROOT>/.claude/skills/beril-paper-writer/tools/'s parent
# (i.e., the skill root). Will be set in main().
SKILL_DIR=""
TOOLS_DIR=""
PROMPTS_DIR=""
REFERENCES_DIR=""
PYTHON_BIN=""

# Discover the Python interpreter that has the package's runtime deps
# (nbformat, python-docx) installed. The bash orchestrator must NOT use
# `python3` directly because system python3 may be different from the
# pipx venv that holds the deps (very common on macOS with Homebrew
# Python being PEP 668 locked).
#
# Resolution order:
#   1. $BERIL_PAPER_WRITER_PYTHON env var (escape hatch — user can
#      point at any python with the deps installed)
#   2. Read the shebang of `which beril-paper-writer` — for pipx
#      installs, this resolves to the venv python that has the deps
#   3. Fall back to system `python3` with a warning that helper scripts
#      may ImportError on missing deps
discover_python_bin() {
    if [[ -n "${BERIL_PAPER_WRITER_PYTHON:-}" ]]; then
        if [[ -x "$BERIL_PAPER_WRITER_PYTHON" ]]; then
            echo "$BERIL_PAPER_WRITER_PYTHON"
            return 0
        fi
        log_warn "BERIL_PAPER_WRITER_PYTHON=$BERIL_PAPER_WRITER_PYTHON is not executable; falling back."
    fi

    local cli_path
    cli_path="$(command -v beril-paper-writer 2>/dev/null || true)"
    if [[ -n "$cli_path" && -x "$cli_path" ]]; then
        local shebang
        shebang="$(head -1 "$cli_path" 2>/dev/null)"
        if [[ "$shebang" == \#!* ]]; then
            local interpreter="${shebang#\#!}"
            # Trim leading whitespace.
            interpreter="${interpreter#"${interpreter%%[![:space:]]*}"}"
            # Take the first word.
            local first_word="${interpreter%% *}"
            if [[ "$first_word" == "/usr/bin/env" ]]; then
                # /usr/bin/env-style shebang — interpreter is the second word.
                local rest="${interpreter#* }"
                local second_word="${rest%% *}"
                interpreter="$(command -v "$second_word" 2>/dev/null || true)"
            else
                interpreter="$first_word"
            fi
            if [[ -n "$interpreter" && -x "$interpreter" ]]; then
                echo "$interpreter"
                return 0
            fi
        fi
    fi

    log_warn "Could not locate the pipx venv's Python; falling back to system python3."
    log_warn "If you hit ImportError for nbformat / python-docx, set:"
    log_warn "  export BERIL_PAPER_WRITER_PYTHON=\$(pipx environment --value PIPX_LOCAL_VENVS)/beril-paper-writer-skill/bin/python"
    local sysp
    sysp="$(command -v python3 2>/dev/null || true)"
    if [[ -z "$sysp" ]]; then
        log_error "python3 not on PATH; aborting"
        exit 3
    fi
    echo "$sysp"
}

# Initialize SKILL_DIR-derived paths.
init_skill_paths() {
    local tools_dir
    tools_dir="$(discover_skill_dir)"
    TOOLS_DIR="$tools_dir"
    SKILL_DIR="$(cd "$tools_dir/.." && pwd)"
    PROMPTS_DIR="$SKILL_DIR/prompts"
    REFERENCES_DIR="$SKILL_DIR/references"

    if [[ ! -d "$PROMPTS_DIR" ]]; then
        log_error "Skill prompts dir missing: $PROMPTS_DIR"
        log_error "Run 'beril-paper-writer install-skill' to set up the skill."
        exit 3
    fi

    PYTHON_BIN="$(discover_python_bin)"
}

# ==============================================================================
# State.json helpers (delegated to paper_writer_helpers.py for atomicity)
# ==============================================================================

read_state_phase() {
    local draft_dir="$1"
    local state_file="$draft_dir/state.json"
    if [[ ! -f "$state_file" ]]; then
        echo "init"
        return
    fi
    "$PYTHON_BIN" -c "
import json, sys
try:
    with open('$state_file') as f:
        print(json.load(f).get('phase', 'init'))
except Exception:
    print('init')
"
}

set_state_phase() {
    local draft_dir="$1"
    local phase="$2"
    "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" update-state \
        "$draft_dir" --phase "$phase" >/dev/null 2>&1
}

read_state_field() {
    local draft_dir="$1"
    local field="$2"
    local state_file="$draft_dir/state.json"
    [[ ! -f "$state_file" ]] && { echo ""; return; }
    "$PYTHON_BIN" -c "
import json, sys
try:
    with open('$state_file') as f:
        d = json.load(f)
    keys = '$field'.split('.')
    v = d
    for k in keys:
        v = v.get(k) if isinstance(v, dict) else None
        if v is None: break
    print('' if v is None else v)
except Exception:
    print('')
"
}

# ==============================================================================
# Claude invocation (mirrors adversarial's pattern)
# ==============================================================================

# invoke_claude <sys_prompt_file> <user_prompt> <model> <expected_write_path>
#               <metadata_path> <label>
# Pipes through stream_progress.py for Write verification + cost summary.
# Returns parser exit code (0 success / 2 silent-failure-retryable / 3 wrong-path).
invoke_claude() {
    local sys_prompt_file="$1"
    local user_prompt="$2"
    local model="$3"
    local expected_write_path="$4"
    local metadata_path="$5"
    local label="$6"

    if ! command -v claude &>/dev/null; then
        log_error "'claude' CLI is not installed or not in PATH"
        return 3
    fi

    local sys_prompt
    sys_prompt="$(cat "$sys_prompt_file")"

    local use_parser=1
    if [[ "${NO_STREAM:-0}" == "1" ]]; then
        use_parser=0
    elif [[ -z "$expected_write_path" ]]; then
        use_parser=0
    elif [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
        log_warn "Python interpreter not resolved; running without stream parser"
        use_parser=0
    elif [[ ! -f "$TOOLS_DIR/stream_progress.py" ]]; then
        log_warn "stream_progress.py missing; running without stream parser"
        use_parser=0
    fi

    if [[ "$use_parser" == "1" ]]; then
        local log_file="${expected_write_path}.stream.log"
        set -o pipefail
        CLAUDECODE= claude -p \
            --model "$model" \
            --system-prompt "$sys_prompt" \
            --allowedTools "$CLAUDE_TOOLS" \
            --dangerously-skip-permissions \
            --output-format stream-json \
            --verbose \
            "$user_prompt" \
            < /dev/null \
            | "$PYTHON_BIN" "$TOOLS_DIR/stream_progress.py" \
                --expected-write-path "$expected_write_path" \
                --log "$log_file" \
                --model "$model" \
                --metadata-out "$metadata_path" \
                --label "$label" \
                > /dev/null
        local rc=$?
        [[ $rc -eq 0 ]] && rm -f "$log_file"
        return $rc
    else
        CLAUDECODE= claude -p \
            --model "$model" \
            --system-prompt "$sys_prompt" \
            --allowedTools "$CLAUDE_TOOLS" \
            --dangerously-skip-permissions \
            "$user_prompt" \
            < /dev/null
    fi
}

# invoke_claude_with_retry: bounded retry on parser exit-code 2 (Write
# never invoked — stochastic failure mode worth retrying). Other non-zero
# codes are non-retryable. Three attempts.
invoke_claude_with_retry() {
    local sys_prompt_file="$1"
    local base_prompt="$2"
    local model="$3"
    local expected_path="$4"
    local label="$5"
    local metadata_path="$6"

    local MAX=3
    local attempt=1
    local rc

    while [[ $attempt -le $MAX ]]; do
        local prompt="$base_prompt"
        if [[ $attempt -gt 1 ]]; then
            log_warn "Retry $attempt/$MAX for $label (previous attempt did not invoke Write)"
            prompt="ATTEMPT $attempt OF $MAX — the previous attempt produced output but did not call the Write tool. The result was lost. THIS ATTEMPT MUST CALL THE Write TOOL with the absolute path. Do not produce the result as a chat response.

${base_prompt}"
        fi

        invoke_claude "$sys_prompt_file" "$prompt" "$model" \
            "$expected_path" "$metadata_path" "$label"
        rc=$?

        case $rc in
            0)  return 0 ;;
            2)  rm -f "$expected_path"; attempt=$((attempt + 1)) ;;
            3)  log_error "$label invoked Write on the wrong path (not retryable)"
                rm -f "$expected_path"
                return 1 ;;
            *)  log_error "$label failed (exit $rc)"
                rm -f "$expected_path"
                return 1 ;;
        esac
    done

    log_error "$label failed to invoke Write across $MAX attempts."
    log_error "  Stream log preserved at: ${expected_path}.stream.log"
    return 1
}

# ==============================================================================
# Phase: init — preflight, mkdir draft_dir, init reframing_log, init state.json
# ==============================================================================

phase_init() {
    local draft_dir="$1"
    local project_id="$2"

    log_phase "Phase: init"

    mkdir -p "$draft_dir/audit" "$draft_dir/figures" "$draft_dir/reviews"

    "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" init-reframing-log "$draft_dir" >/dev/null

    # Initialize state.json with project_id.
    "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" update-state \
        "$draft_dir" --phase "init" >/dev/null
    "$PYTHON_BIN" -c "
import json
with open('$draft_dir/state.json') as f:
    d = json.load(f)
d['project_id'] = '$project_id'
d['mode'] = '${PAPER_WRITER_MODE:-paper}'
with open('$draft_dir/state.json', 'w') as f:
    json.dump(d, f, indent=2, sort_keys=True)
"
    log_ok "draft_dir initialized: $draft_dir"
}

# ==============================================================================
# Halted-handoff emission
# ==============================================================================
#
# Mid-pipeline failures (draft_dir exists) write a `phase=halted` handoff
# JSON before exiting non-zero. Symmetric with the pause emitters: the
# slash-command parser can read .handoff.json after every bash call and
# know what happened (paused vs halted) without parsing stderr.
#
# Pre-init failures (no draft_dir context) skip this and surface stderr
# only — nothing to write into.

emit_halted_handoff() {
    local draft_dir="$1"
    local reason="$2"
    local recovery_hint="${3:-}"

    if [[ ! -d "$draft_dir" ]]; then
        # Pre-init or draft_dir already gone — can't write handoff.
        return 0
    fi

    local current_phase
    current_phase="$(read_state_phase "$draft_dir")"

    local prompt="Pipeline halted at phase=$current_phase: $reason"
    if [[ -n "$recovery_hint" ]]; then
        prompt="$prompt
Recovery: $recovery_hint"
    fi

    "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" write-handoff \
        "$draft_dir" \
        --phase halted \
        --prompt-to-user "$prompt" \
        > /dev/null 2>&1 || true   # never let handoff-write itself break the exit path

    # Note: do NOT mutate state.json's phase to "halted". The state's phase
    # field records "where we were working when we halted" — resume needs
    # that to know which phase to retry idempotently. The .handoff.json's
    # phase=halted is the one telling the slash-command parser the run
    # halted; state.json stays at its in-progress value.
}

# acquire_draft_lock <draft_dir> <verb> — best-effort PID-file mutex.
#
# Replaces an earlier flock-based design after live testing on macOS
# revealed flock isn't shipped by default (would have required `brew
# install flock`, an unwanted external dep). The Python helper does
# liveness-checked PID-file write via fcntl-free atomic-create.
#
# Prevents two concurrent paper_writer processes from racing on the
# same draft_dir's phase functions. Race window of ~1ms; documented
# as best-effort, not POSIX-strict mutex. The trap on EXIT/INT/TERM
# (set in main(), one trap per script invocation) calls release-lock
# to remove the lock file when the orchestrator exits.
acquire_draft_lock() {
    local d="$1"
    local verb="$2"
    if [[ ! -d "$d" ]]; then
        # Pre-init: lock target dir may not exist yet. Skip; phase_init
        # creates the dir and the next acquire attempt will succeed.
        return 0
    fi
    if ! "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" acquire-lock \
            "$d" --verb "$verb" --pid "$$"; then
        return 1
    fi
    # Set the cleanup trap: on script exit (normal or via signal), call
    # release-lock to remove the lock file. The trap is set ONCE per
    # script invocation; subsequent draft_dirs won't re-trap (acceptable
    # since we hold one lock per invocation).
    trap "'$PYTHON_BIN' '$TOOLS_DIR/paper_writer_helpers.py' release-lock '$d' 2>/dev/null || true" EXIT INT TERM
    return 0
}

# halt_with <draft_dir> <reason> [<hint>] — emit halted handoff and exit 2.
# Default hint suggests `beril-paper-writer continue` for resume.
halt_with() {
    local d="$1"
    local reason="$2"
    local hint="${3:-Re-run: beril-paper-writer continue $d  (phases are idempotent; will retry the failed step)}"
    emit_halted_handoff "$d" "$reason" "$hint"
    exit 2
}

# ==============================================================================
# Phase: extract — run extract_methods.py + extract_figures.py
# ==============================================================================

phase_extract() {
    local project_root="$1"
    local draft_dir="$2"

    log_phase "Phase: extract"

    if [[ -f "$draft_dir/methods_provenance.md" ]]; then
        log_step "methods_provenance.md exists, skipping extract_methods.py"
    else
        log_step "Running extract_methods.py"
        if ! "$PYTHON_BIN" "$TOOLS_DIR/extract_methods.py" "$project_root" \
                --output-dir "$draft_dir" 2>&1 | tee -a "$draft_dir/audit/extract_methods.log"; then
            local rc=${PIPESTATUS[0]}
            if [[ $rc -eq 1 ]]; then
                log_error "extract_methods.py: no notebooks found at $project_root/notebooks/"
                log_error "Halting per LAYOUT 'Extract-tool invocation' contract (exit 1 = halt)."
                return 1
            fi
            log_warn "extract_methods.py exited $rc; some notebooks may have failed parse. Continuing."
        fi
    fi

    if [[ -f "$draft_dir/figures_inventory.md" ]]; then
        log_step "figures_inventory.md exists, skipping extract_figures.py"
    else
        log_step "Running extract_figures.py"
        "$PYTHON_BIN" "$TOOLS_DIR/extract_figures.py" "$project_root" \
            --output-dir "$draft_dir" 2>&1 | tee -a "$draft_dir/audit/extract_figures.log" \
            || log_warn "extract_figures.py exited non-zero; figures inventory may be empty (not a hard failure)."
    fi

    log_ok "extract phase complete"
}

# ==============================================================================
# Phase: plan — run plan.v1.md → throughline_candidates.md, then check_glyphs
# ==============================================================================

phase_plan() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"

    log_phase "Phase: plan (plan.v1 → throughline_candidates.md)"

    local target="$draft_dir/throughline_candidates.md"
    if [[ -f "$target" ]]; then
        log_step "throughline_candidates.md exists, skipping plan.v1 invocation"
    else
        local user_prompt
        user_prompt="Run the Plan-phase agent against the project at $project_root to produce $target. The system prompt above describes your discipline; this user-prompt provides concrete inputs.

## Inputs

- \`PROJECT_ROOT\` = \`$project_root\`
- \`DRAFT_DIR\` = \`$draft_dir\`
- \`THROUGHLINE_CANDIDATES_PATH\` = \`$target\`  (your output goes here)
- \`REPORT_PATH\` = \`$project_root/REPORT.md\`
- \`RESEARCH_PLAN_PATH\` = \`$project_root/RESEARCH_PLAN.md\`
- \`NOTEBOOKS_DIR\` = \`$project_root/notebooks\`
- \`ANALYSIS_REQUESTS_PATH\` = \`$draft_dir/analysis_requests.md\`  (will be empty unless REPORT triggers gap-fill)

No \`FIGURES_INVENTORY_PATH\` (optional input; not provided unless extract_figures.py succeeded).
No \`MODE_OVERRIDE\` (let triage decide; default mode follows tier).
No \`RE_EVALUATION_MODE\` (this is a fresh first-pass run).

## What you should do

1. Read REPORT_PATH (canonical findings — read fully), then RESEARCH_PLAN_PATH (design intent), then individual notebooks from NOTEBOOKS_DIR for sub-claim grounding as needed.
2. Triage the project as STRONG / THIN / EXPLORATORY per the rubric in your system prompt. Name the specific evidence-strength criteria the project meets or misses; rubric-driven, not vibes.
3. Extract 2-3 candidate throughlines per tier-aware extraction rules. For THIN tier, also produce the +1 narrowed-claim candidate per SPEC §3.3.
4. Build per-candidate evidence maps with strength glyphs (✓ direct / ⚠ partial / ✗ contradicts / ◇ orthogonal). Operationalize each glyph against the source — no inflation.
5. Build per-candidate weakness inventories (project-specific, not generic) and 'what this paper would NOT include if chosen' lists.
6. Run the self-review pass; fix any issues before writing.
7. Write THROUGHLINE_CANDIDATES_PATH via the Write tool.
8. Pause and exit with the closing-message template (drafting mode)."

        invoke_claude_with_retry \
            "$PROMPTS_DIR/plan.v1.md" "$user_prompt" "$model" \
            "$target" "plan.v1" "$draft_dir/audit/plan.metadata.json" \
            || return 1
    fi

    if [[ ! -f "$target" ]]; then
        log_error "plan.v1 produced no $target after retries; aborting"
        return 1
    fi

    # Strength-glyph cross-walk post-processor (advisory).
    log_step "Running check_throughline_glyphs.py (advisory cross-walk)"
    local glyph_warnings_file="$draft_dir/audit/glyph_warnings.txt"
    "$PYTHON_BIN" "$TOOLS_DIR/check_throughline_glyphs.py" "$target" 2> "$glyph_warnings_file" || true
    if grep -q "^\[check_throughline_glyphs\] WARN" "$glyph_warnings_file"; then
        log_warn "Strength-glyph cross-walk warnings detected (will surface in handoff):"
        grep "^\[check_throughline_glyphs\] WARN" "$glyph_warnings_file" | sed 's/^/  /' >&2
    fi

    log_ok "plan phase complete"
}

# ==============================================================================
# Pause: emit handoff for throughline_pick
# ==============================================================================

emit_throughline_handoff() {
    local draft_dir="$1"
    local target="$draft_dir/throughline_candidates.md"

    log_phase "Pause: throughline_pick"

    # Build the choices JSON from `## Candidate TLN: <title>` headers in
    # throughline_candidates.md. Use a Python helper to write the JSON
    # correctly (avoids bash's word-splitting fragility on titles with
    # spaces, quotes, em-dashes, emoji, etc. — the live-run failure mode
    # from 2026-04-26).
    local choices_json="$draft_dir/.handoff_choices.json"
    "$PYTHON_BIN" - "$target" "$choices_json" <<'PYEOF'
import json, re, sys
src, dst = sys.argv[1], sys.argv[2]
with open(src, encoding="utf-8") as f:
    text = f.read()
choices = []
for m in re.finditer(r"^## Candidate (TL\d+):\s*(.+)$", text, re.MULTILINE):
    cid = m.group(1)
    label = m.group(2).strip()
    if len(label) > 140:
        label = label[:137] + "..."
    choices.append({"id": cid, "label": label})
with open(dst, "w", encoding="utf-8") as f:
    json.dump(choices, f, ensure_ascii=False, indent=2)
print(f"wrote {len(choices)} candidates → {dst}", file=sys.stderr)
PYEOF
    local choices_count
    choices_count=$("$PYTHON_BIN" -c "import json; print(len(json.load(open('$choices_json'))))" 2>/dev/null || echo 0)
    if [[ "$choices_count" -eq 0 ]]; then
        log_error "No candidates parsed from $target; cannot emit pick handoff."
        log_error "File contents are likely malformed; review manually."
        "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" write-handoff "$draft_dir" \
            --phase halted \
            --prompt-to-user "plan.v1 wrote $target but no '## Candidate TLN:' headers were found. Inspect the file manually before resuming." \
            || log_warn "halted handoff write-handoff also failed; .handoff.json may be stale"
        return 1
    fi

    # Build advisory-warnings JSON from the glyph-check output. Same JSON-
    # file-passing pattern — warning text contains square brackets, colons,
    # commas that break bash word-splitting.
    local warnings_json="$draft_dir/.handoff_warnings.json"
    "$PYTHON_BIN" - "$draft_dir/audit/glyph_warnings.txt" "$warnings_json" <<'PYEOF'
import json, sys
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
warnings = []
if src.is_file():
    for line in src.read_text(encoding="utf-8").splitlines():
        if line.startswith("[check_throughline_glyphs] WARN"):
            warnings.append(line.replace("[check_throughline_glyphs] ", "", 1))
with open(dst, "w", encoding="utf-8") as f:
    json.dump(warnings, f, ensure_ascii=False, indent=2)
print(f"wrote {len(warnings)} warnings → {dst}", file=sys.stderr)
PYEOF

    local resume_cmd="beril-paper-writer continue $draft_dir --pick TL{N} [--revision \"text\"]"
    if ! "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" write-handoff "$draft_dir" \
            --phase throughline_pick \
            --prompt-to-user "Three throughline candidates produced. Review the candidates at $target, pick one (optionally with a revision note), then resume drafting." \
            --choices-json "$choices_json" \
            --advisory-warnings-json "$warnings_json" \
            --candidates-path "$target" \
            --resume-command "$resume_cmd"; then
        log_error "write-handoff for throughline_pick FAILED"
        log_error "  This is a fatal error — slash-command parser cannot drive the pick UX without a valid handoff."
        log_error "  Tempfiles: $choices_json, $warnings_json"
        return 1
    fi

    set_state_phase "$draft_dir" "throughline_pick"

    # Cleanup tempfiles on success (keep on failure for debugging — the
    # error path returns before this).
    rm -f "$choices_json" "$warnings_json"

    echo "" >&2
    echo "PAUSE: throughline_pick" >&2
    echo "  Handoff: $draft_dir/.handoff.json" >&2
    echo "  Resume:  $resume_cmd" >&2
}

# ==============================================================================
# Phase: citation_pool — run citation_pool.v1, then formatter
# ==============================================================================

phase_citation_pool() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"

    log_phase "Phase: citation_pool"

    local pool_json="$draft_dir/pool.json"
    local references_md="$draft_dir/references.md"

    if [[ -f "$references_md" && -f "$draft_dir/bibliography.bib" ]]; then
        log_step "references.md and bibliography.bib exist, skipping citation_pool"
        return 0
    fi

    if [[ ! -f "$pool_json" ]]; then
        local existing_refs="$project_root/references.md"
        local existing_refs_clause=""
        if [[ -f "$existing_refs" ]]; then
            existing_refs_clause="
- \`EXISTING_REFERENCES_MD\` = \`$existing_refs\`  (curated seed; verify before pooling)"
        fi

        local user_prompt
        user_prompt="Run the citation_pool.v1 agent against the project at $project_root to produce $pool_json.

## Inputs

- \`PROJECT_ROOT\` = \`$project_root\`
- \`DRAFT_DIR\` = \`$draft_dir\`
- \`POOL_JSON_PATH\` = \`$pool_json\`  (your output goes here)
- \`THROUGHLINE_PATH\` = \`$draft_dir/00_throughline.md\`
- \`REPORT_PATH\` = \`$project_root/REPORT.md\`
- \`RESEARCH_PLAN_PATH\` = \`$project_root/RESEARCH_PLAN.md\`$existing_refs_clause
- \`MAX_BUDGET\` = \`30\`
- \`DEPTH\` = \`${PAPER_WRITER_DEPTH:-$DEFAULT_DEPTH}\`

Build the citation pool per your system prompt's verification discipline. Write POOL_JSON_PATH via the Write tool, then emit the closing-message template."

        invoke_claude_with_retry \
            "$PROMPTS_DIR/citation_pool.v1.md" "$user_prompt" "$model" \
            "$pool_json" "citation_pool.v1" "$draft_dir/audit/citation_pool.metadata.json" \
            || return 1
    fi

    log_step "Running citation_pool.py validate"
    if ! "$PYTHON_BIN" "$TOOLS_DIR/citation_pool.py" validate "$pool_json" \
            > "$draft_dir/audit/citation_pool_validate.log" 2>&1; then
        log_warn "citation_pool.py validate reported errors (see audit log); continuing to format step anyway."
    fi

    log_step "Running citation_pool.py format"
    if ! "$PYTHON_BIN" "$TOOLS_DIR/citation_pool.py" format "$pool_json" "$draft_dir" \
            >> "$draft_dir/audit/citation_pool_validate.log" 2>&1; then
        log_error "citation_pool.py format failed (see $draft_dir/audit/citation_pool_validate.log)"
        return 1
    fi

    log_ok "citation_pool phase complete"
}

# ==============================================================================
# Phase: methods — fill AI_DISCLOSURE template, then run methods.v1
# ==============================================================================

phase_methods() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"
    local project_id="$4"

    log_phase "Phase: methods"

    local target="$draft_dir/01_methods.md"
    if [[ -f "$target" ]]; then
        log_step "01_methods.md exists, skipping methods.v1"
        return 0
    fi

    # Fill AI_DISCLOSURE template.
    local pw_version
    pw_version="$("$PYTHON_BIN" -c 'from beril_paper_writer import __version__; print(__version__)' 2>/dev/null || echo '0.1.0')"
    local sha
    sha="$(read_state_field "$draft_dir" "throughline.artifact_hash_at_confirmation" | cut -c1-7)"
    [[ -z "$sha" ]] && sha="snapshot"
    local rewrites
    rewrites="$(read_state_field "$draft_dir" "iteration.rewrite_passes")"
    [[ -z "$rewrites" ]] && rewrites="0"

    local ai_disc_filled="$draft_dir/.ai_disclosure_filled.md"
    "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" fill-template \
        "$REFERENCES_DIR/ai_disclosure_template.md" \
        "$ai_disc_filled" \
        --var paper_writer_version="$pw_version" \
        --var model_id="$model" \
        --var project_id="$project_id" \
        --var sha="$sha" \
        --var N="$rewrites" \
        > /dev/null

    local ai_disc_body
    ai_disc_body="$(cat "$ai_disc_filled")"

    local mode tier
    mode="$(read_state_field "$draft_dir" "mode")"
    [[ -z "$mode" ]] && mode="paper"
    tier="$(read_state_field "$draft_dir" "tier")"
    [[ -z "$tier" ]] && tier="STRONG"

    local user_prompt
    user_prompt="Run methods.v1 against the extracted provenance file to produce $target.

## Inputs

- \`PROJECT_ROOT\` = \`$project_root\`
- \`DRAFT_DIR\` = \`$draft_dir\`
- \`METHODS_OUT_PATH\` = \`$target\`
- \`METHODS_PROVENANCE_PATH\` = \`$draft_dir/methods_provenance.md\`
- \`RESEARCH_PLAN_PATH\` = \`$project_root/RESEARCH_PLAN.md\`
- \`REPORT_PATH\` = \`$project_root/REPORT.md\`
- \`THROUGHLINE_PATH\` = \`$draft_dir/00_throughline.md\`
- \`REFRAMING_LOG_PATH\` = \`$draft_dir/reframing_log.md\`
- \`MODE\` = \`$mode\`
- \`TIER\` = \`$tier\`
- \`AI_DISCLOSURE_TEMPLATE\` (verbatim, insert under 'AI-Assisted Analysis' — do NOT rewrite or paraphrase):

$ai_disc_body

(end of AI_DISCLOSURE_TEMPLATE)

No \`REPAIR_MODE\` (this is a fresh drafting run).

Read METHODS_PROVENANCE_PATH (the factual anchor), RESEARCH_PLAN_PATH (design intent), THROUGHLINE_PATH (which methods are load-bearing), then REPORT_PATH for context only. Write METHODS_OUT_PATH via the Write tool, then emit the closing-message template."

    invoke_claude_with_retry \
        "$PROMPTS_DIR/methods.v1.md" "$user_prompt" "$model" \
        "$target" "methods.v1" "$draft_dir/audit/methods.metadata.json" \
        || return 1

    log_ok "methods phase complete"
}

# ==============================================================================
# Phase: results — run results.v1; orchestrator copies named figures after
# ==============================================================================

phase_results() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"

    log_phase "Phase: results"

    local target="$draft_dir/02_results.md"
    if [[ -f "$target" ]]; then
        log_step "02_results.md exists, skipping results.v1"
        return 0
    fi

    local figures_inventory="$draft_dir/figures_inventory.md"
    [[ ! -f "$figures_inventory" ]] && figures_inventory=""

    local mode tier
    mode="$(read_state_field "$draft_dir" "mode")"; [[ -z "$mode" ]] && mode="paper"
    tier="$(read_state_field "$draft_dir" "tier")"; [[ -z "$tier" ]] && tier="STRONG"

    local figures_clause=""
    if [[ -n "$figures_inventory" ]]; then
        figures_clause="
- \`FIGURES_INVENTORY_PATH\` = \`$figures_inventory\`"
    fi

    local user_prompt
    user_prompt="Run results.v1 to produce $target.

## Inputs

- \`PROJECT_ROOT\` = \`$project_root\`
- \`DRAFT_DIR\` = \`$draft_dir\`
- \`RESULTS_OUT_PATH\` = \`$target\`
- \`THROUGHLINE_PATH\` = \`$draft_dir/00_throughline.md\`
- \`METHODS_PATH\` = \`$draft_dir/01_methods.md\`
- \`METHODS_PROVENANCE_PATH\` = \`$draft_dir/methods_provenance.md\`
- \`REPORT_PATH\` = \`$project_root/REPORT.md\`
- \`RESEARCH_PLAN_PATH\` = \`$project_root/RESEARCH_PLAN.md\`
- \`REFRAMING_LOG_PATH\` = \`$draft_dir/reframing_log.md\`$figures_clause
- \`MODE\` = \`$mode\`
- \`TIER\` = \`$tier\`

Write RESULTS_OUT_PATH via the Write tool. Emit closing-message template naming the K selected figures."

    invoke_claude_with_retry \
        "$PROMPTS_DIR/results.v1.md" "$user_prompt" "$model" \
        "$target" "results.v1" "$draft_dir/audit/results.metadata.json" \
        || return 1

    # Figure copy: best-effort. results.v1's closing message names the
    # figures; in MVP we just copy any figures referenced as fig0N_* in
    # 02_results.md from the project's figures/ dir to draft_dir/figures/.
    log_step "Copying selected figures to $draft_dir/figures/"
    local fig_count=0
    while IFS= read -r figname; do
        local src="$project_root/figures/$figname"
        if [[ -f "$src" ]]; then
            cp "$src" "$draft_dir/figures/$figname" 2>/dev/null && fig_count=$((fig_count + 1))
        fi
    done < <(grep -oE '\bfig[0-9]+_[a-zA-Z0-9_-]+\.(png|svg|pdf|jpg)' "$target" 2>/dev/null | sort -u)
    log_step "Copied $fig_count figure(s)"

    log_ok "results phase complete"
}

# ==============================================================================
# Phase: discussion — run discussion.v1
# ==============================================================================

phase_discussion() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"

    log_phase "Phase: discussion"

    local target="$draft_dir/03_discussion.md"
    if [[ -f "$target" ]]; then
        log_step "03_discussion.md exists, skipping discussion.v1"
        return 0
    fi

    local mode tier
    mode="$(read_state_field "$draft_dir" "mode")"; [[ -z "$mode" ]] && mode="paper"
    tier="$(read_state_field "$draft_dir" "tier")"; [[ -z "$tier" ]] && tier="STRONG"

    local user_prompt
    user_prompt="Run discussion.v1 to produce $target.

## Inputs

- \`PROJECT_ROOT\` = \`$project_root\`
- \`DRAFT_DIR\` = \`$draft_dir\`
- \`DISCUSSION_OUT_PATH\` = \`$target\`
- \`THROUGHLINE_PATH\` = \`$draft_dir/00_throughline.md\`
- \`METHODS_PATH\` = \`$draft_dir/01_methods.md\`
- \`RESULTS_PATH\` = \`$draft_dir/02_results.md\`
- \`REFERENCES_MD_PATH\` = \`$draft_dir/references.md\`
- \`POOL_JSON_PATH\` = \`$draft_dir/pool.json\`
- \`REPORT_PATH\` = \`$project_root/REPORT.md\`
- \`RESEARCH_PLAN_PATH\` = \`$project_root/RESEARCH_PLAN.md\`
- \`REFRAMING_LOG_PATH\` = \`$draft_dir/reframing_log.md\`
- \`MODE\` = \`$mode\`
- \`TIER\` = \`$tier\`
- \`POOL_EXHAUSTION_DEFAULT\` = \`scope-down\`  (MVP: pump-through; v0.2 adds user-pause for citation-request / accept-as-limitation)

Write DISCUSSION_OUT_PATH via Write. Emit closing-message template; if [NEEDS CITATION] count > 0, the orchestrator will surface it but the MVP applies POOL_EXHAUSTION_DEFAULT (scope-down) — discussion.v1 should reframe rather than emit raw [NEEDS CITATION] placeholders when feasible."

    invoke_claude_with_retry \
        "$PROMPTS_DIR/discussion.v1.md" "$user_prompt" "$model" \
        "$target" "discussion.v1" "$draft_dir/audit/discussion.metadata.json" \
        || return 1

    log_ok "discussion phase complete"
}

# ==============================================================================
# Phase: intro — run intro.v1
# ==============================================================================

phase_intro() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"

    log_phase "Phase: intro"

    local target="$draft_dir/04_introduction.md"
    if [[ -f "$target" ]]; then
        log_step "04_introduction.md exists, skipping intro.v1"
        return 0
    fi

    local mode tier
    mode="$(read_state_field "$draft_dir" "mode")"; [[ -z "$mode" ]] && mode="paper"
    tier="$(read_state_field "$draft_dir" "tier")"; [[ -z "$tier" ]] && tier="STRONG"

    local user_prompt
    user_prompt="Run intro.v1 to produce $target.

## Inputs

- \`PROJECT_ROOT\` = \`$project_root\`
- \`DRAFT_DIR\` = \`$draft_dir\`
- \`INTRO_OUT_PATH\` = \`$target\`
- \`THROUGHLINE_PATH\` = \`$draft_dir/00_throughline.md\`
- \`DISCUSSION_PATH\` = \`$draft_dir/03_discussion.md\`
- \`RESULTS_PATH\` = \`$draft_dir/02_results.md\`
- \`REFERENCES_MD_PATH\` = \`$draft_dir/references.md\`
- \`REPORT_PATH\` = \`$project_root/REPORT.md\`
- \`RESEARCH_PLAN_PATH\` = \`$project_root/RESEARCH_PLAN.md\`
- \`REFRAMING_LOG_PATH\` = \`$draft_dir/reframing_log.md\`
- \`MODE\` = \`$mode\`
- \`TIER\` = \`$tier\`

Write INTRO_OUT_PATH via Write. Emit closing-message template."

    invoke_claude_with_retry \
        "$PROMPTS_DIR/intro.v1.md" "$user_prompt" "$model" \
        "$target" "intro.v1" "$draft_dir/audit/intro.metadata.json" \
        || return 1

    log_ok "intro phase complete"
}

# ==============================================================================
# Phase: abstract — run abstract.v1
# ==============================================================================

phase_abstract() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"

    log_phase "Phase: abstract"

    local target="$draft_dir/05_abstract.md"
    if [[ -f "$target" ]]; then
        log_step "05_abstract.md exists, skipping abstract.v1"
        return 0
    fi

    local mode tier
    mode="$(read_state_field "$draft_dir" "mode")"; [[ -z "$mode" ]] && mode="paper"
    tier="$(read_state_field "$draft_dir" "tier")"; [[ -z "$tier" ]] && tier="STRONG"

    local user_prompt
    user_prompt="Run abstract.v1 to produce $target.

## Inputs

- \`PROJECT_ROOT\` = \`$project_root\`
- \`DRAFT_DIR\` = \`$draft_dir\`
- \`ABSTRACT_OUT_PATH\` = \`$target\`
- \`THROUGHLINE_PATH\` = \`$draft_dir/00_throughline.md\`
- \`INTRO_PATH\` = \`$draft_dir/04_introduction.md\`
- \`METHODS_PATH\` = \`$draft_dir/01_methods.md\`
- \`RESULTS_PATH\` = \`$draft_dir/02_results.md\`
- \`DISCUSSION_PATH\` = \`$draft_dir/03_discussion.md\`
- \`REFRAMING_LOG_PATH\` = \`$draft_dir/reframing_log.md\`
- \`MODE\` = \`$mode\`
- \`TIER\` = \`$tier\`

Write ABSTRACT_OUT_PATH via Write. Emit closing-message template."

    invoke_claude_with_retry \
        "$PROMPTS_DIR/abstract.v1.md" "$user_prompt" "$model" \
        "$target" "abstract.v1" "$draft_dir/audit/abstract.metadata.json" \
        || return 1

    log_ok "abstract phase complete"
}

# ==============================================================================
# Phase: data_avail — orchestrator-side template fill (no LLM)
# ==============================================================================

phase_data_avail() {
    local project_root="$1"
    local draft_dir="$2"
    local project_id="$3"

    log_phase "Phase: data_availability (orchestrator-side template fill)"

    local target="$draft_dir/07_data_availability.md"
    if [[ -f "$target" ]]; then
        log_step "07_data_availability.md exists, skipping fill"
        return 0
    fi

    local pw_version
    pw_version="$("$PYTHON_BIN" -c 'from beril_paper_writer import __version__; print(__version__)' 2>/dev/null || echo '0.1.0')"

    # MVP: defer all the BERDL-specific extraction logic; emit stubs with
    # [TBD] markers per LAYOUT line 424. v0.2 implements proper extraction.
    "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" fill-template \
        "$REFERENCES_DIR/data_availability_template.md" \
        "$target" \
        --var code_repo_url="[CODE REPO: TBD — fill before submission]" \
        --var code_repo_ref="HEAD" \
        --var paper_writer_version="$pw_version" \
        --var kberdl_databases_block="[K-BERDL DATABASES: TBD — orchestrator extraction not yet implemented in v0.1; review methods_provenance.md §'Spark / K-BERDL Queries' and fill manually before submission.]" \
        --var public_accessions_block="[PUBLIC ACCESSIONS: TBD — orchestrator extraction not yet implemented in v0.1; review RESEARCH_PLAN.md §'Data sources' and fill manually before submission.]" \
        --var restricted_access_block="[RESTRICTED ACCESS: TBD — orchestrator extraction not yet implemented in v0.1; default assumption is 'all publicly available' but confirm before submission.]" \
        --var requirements_file_path="requirements.txt" \
        > /dev/null

    log_warn "07_data_availability.md emitted with [TBD] markers (v0.1 limitation; v0.2 fills BERDL-specific blocks)"
    log_ok "data_avail phase complete"
}

# ==============================================================================
# Phase: reframe_drift_audit — invoke reframer.v1 once after all sections drafted
# ==============================================================================
#
# reframer.v1's escape hatches halt if any drafted section is missing, so this
# runs ONCE at end of drafting (not inline after each section per the punch
# list's original recommendation — the prompt's design forced this simpler
# choice). Coverage is equivalent for the C9-pattern (cross-section drift)
# at lower cost (~$0.30 / 30s vs ~$1.50 / 2.5min).

phase_reframe_drift_audit() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"

    log_phase "Phase: reframe_drift_audit"

    # Sanity check: reframer needs all 5 drafted sections.
    local missing=0
    for s in 01_methods.md 02_results.md 03_discussion.md 04_introduction.md 05_abstract.md; do
        if [[ ! -f "$draft_dir/$s" ]]; then
            log_warn "$s missing; skipping reframer (would halt anyway)"
            missing=1
        fi
    done
    if [[ $missing -eq 1 ]]; then
        return 0
    fi

    # Idempotency: skip if reframer already appended entries this run.
    # We check via a sentinel file; if the user runs resume on a fully-
    # drafted-but-not-reframed draft, we DO want to run reframer.
    local sentinel="$draft_dir/audit/reframer.done"
    if [[ -f "$sentinel" ]]; then
        log_step "reframer already ran (sentinel: audit/reframer.done); skipping"
        return 0
    fi

    local user_prompt="Run reframer.v1 to audit drift across the assembled draft sections.

## Inputs

- \`PROJECT_ROOT\` = \`$project_root\`
- \`DRAFT_DIR\` = \`$draft_dir\`
- \`THROUGHLINE_PATH\` = \`$draft_dir/00_throughline.md\`
- \`METHODS_PATH\` = \`$draft_dir/01_methods.md\`
- \`RESULTS_PATH\` = \`$draft_dir/02_results.md\`
- \`DISCUSSION_PATH\` = \`$draft_dir/03_discussion.md\`
- \`INTRO_PATH\` = \`$draft_dir/04_introduction.md\`
- \`ABSTRACT_PATH\` = \`$draft_dir/05_abstract.md\`
- \`REPORT_PATH\` = \`$project_root/REPORT.md\`
- \`RESEARCH_PLAN_PATH\` = \`$project_root/RESEARCH_PLAN.md\`
- \`METHODS_PROVENANCE_PATH\` = \`$draft_dir/methods_provenance.md\`
- \`REFRAMING_LOG_PATH\` = \`$draft_dir/reframing_log.md\`

Walk the 5 drift checks per your system prompt's discipline. Append entries to REFRAMING_LOG_PATH for every drift detected; emit the closing-message template with the entry count."

    # reframer.v1 writes via Edit (append to reframing_log.md), not Write —
    # so we don't pass --expected-write-path. Use NO_STREAM=1 to skip the
    # Write-verification harness. Cost summary still appears via stderr.
    local prior_no_stream="${NO_STREAM:-0}"
    NO_STREAM=1
    invoke_claude \
        "$PROMPTS_DIR/reframer.v1.md" "$user_prompt" "$model" \
        "" "$draft_dir/audit/reframer.metadata.json" "reframer.v1" \
        || log_warn "reframer.v1 invocation returned non-zero; reframing_log.md may be incomplete"
    NO_STREAM="$prior_no_stream"

    # Mark sentinel — reframer ran (whether or not it found drift).
    touch "$sentinel"

    log_ok "reframe_drift_audit phase complete"
}

# ==============================================================================
# Phase: finalize_citations — second-pass renumbering after all sections drafted
# ==============================================================================

phase_finalize_citations() {
    local draft_dir="$1"

    log_phase "Phase: finalize_citations"

    local pool_json="$draft_dir/pool.json"
    if [[ ! -f "$pool_json" ]]; then
        log_warn "pool.json missing; skipping citation finalize (citation_pool phase may have failed)"
        return 0
    fi

    log_step "Walking section files for [bib_key] marks; renumbering references.md"
    if ! "$PYTHON_BIN" "$TOOLS_DIR/citation_pool.py" finalize "$draft_dir" \
            > "$draft_dir/audit/finalize_citations.log" 2>&1; then
        log_warn "citation_pool.py finalize exited non-zero (see audit/finalize_citations.log); continuing"
    fi
    # Surface the orphan-citation count from finalize_warnings.md, if any.
    if [[ -f "$draft_dir/finalize_warnings.md" ]]; then
        local orphan_line
        orphan_line="$(grep -E '^\*\*[0-9]+ orphaned' "$draft_dir/finalize_warnings.md" || true)"
        if [[ -n "$orphan_line" ]]; then
            log_warn "Citation finalize found orphans:"
            echo "  $orphan_line" >&2
            echo "  See $draft_dir/finalize_warnings.md for the full list" >&2
        fi
    fi

    log_ok "finalize_citations phase complete"
}

# ==============================================================================
# Phase: assemble — concat to manuscript.md, run validate_manuscript
# ==============================================================================

phase_assemble() {
    local draft_dir="$1"

    log_phase "Phase: assemble (concat + validate)"

    local manuscript="$draft_dir/manuscript.md"
    local pool_json="$draft_dir/pool.json"
    local use_citation_render=1
    if [[ ! -f "$pool_json" ]]; then
        log_warn "pool.json missing; assembling section files verbatim (no [bib_key] → [N] substitution)"
        use_citation_render=0
    fi

    # Stub title block — orchestrator-owned (no prompt writes a title in
    # v0.1; M1 will fail without this). The H1 must literally read
    # "# Title" (or "# Title page") to match validator's M1 alias-set;
    # the project_id and "TBD: assign title" markers go in prose under
    # the header so they survive into the final output as
    # user-resolves-before-submission flags.
    local project_id
    project_id="$(read_state_field "$draft_dir" "project_id")"; [[ -z "$project_id" ]] && project_id="(unknown_project)"
    local title_block_path="$draft_dir/.title_block.md"
    cat > "$title_block_path" <<EOF
# Title

**Working title:** ${project_id} — DRAFT v0.1 [TBD: assign final title before submission]

**Authors:** [TBD: list authors before submission]

**Affiliations:** [TBD: list affiliations before submission]

**Corresponding author:** [TBD: name + email before submission]

EOF

    rm -f "$manuscript"
    {
        # Title block first — satisfies M1 (Required sections present).
        cat "$title_block_path"
        echo ""
        # IMRAD order per SPEC §6.1, with title/abstract leading.
        # Each section is piped through citation_pool.py render-with-numbers
        # so `[bib_key]` form in the section file gets `[N]` numeric form
        # in manuscript.md (non-destructive: section files keep [bib_key]).
        for section in 05_abstract.md 04_introduction.md 01_methods.md \
                       02_results.md 03_discussion.md 07_data_availability.md \
                       references.md; do
            local f="$draft_dir/$section"
            if [[ -f "$f" ]]; then
                if [[ "$use_citation_render" == "1" ]]; then
                    "$PYTHON_BIN" "$TOOLS_DIR/citation_pool.py" \
                        render-with-numbers "$f" "$pool_json" 2>/dev/null \
                        || cat "$f"   # fall back to verbatim on render error
                else
                    cat "$f"
                fi
                echo ""
                echo ""
            else
                log_warn "Section file missing during assembly: $section"
            fi
        done
    } > "$manuscript"

    log_step "Running validate_manuscript.py"
    local mode
    mode="$(read_state_field "$draft_dir" "mode")"; [[ -z "$mode" ]] && mode="paper"
    "$PYTHON_BIN" "$TOOLS_DIR/validate_manuscript.py" "$draft_dir" \
        --mode "$mode" --output "$draft_dir/audit/validation.json" \
        > "$draft_dir/audit/validation.log" 2>&1 || \
        log_warn "validate_manuscript.py reported failures (see $draft_dir/audit/validation.json); not auto-fixing in MVP"

    # Cleanup transient title-block tempfile (manuscript.md has the title).
    rm -f "$title_block_path"

    log_ok "assemble phase complete: $manuscript"
}

# ==============================================================================
# Phase: review — single-pass adversarial OR fallback
# ==============================================================================

phase_review() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"
    local project_id="$4"

    log_phase "Phase: review (single-pass; no rewrite loop in MVP)"

    if [[ -d "$draft_dir/reviews" ]] && ls "$draft_dir/reviews/"draft_*_review_*.md >/dev/null 2>&1; then
        log_step "Existing review found; skipping new review pass"
        return 0
    fi

    if [[ "${NO_ADVERSARIAL:-0}" == "1" ]]; then
        log_step "--no-adversarial set; using fallback inline reviewer"
        run_fallback_reviewer "$project_root" "$draft_dir" "$model" || return 1
        return 0
    fi

    if command -v beril-adversarial-cli &>/dev/null; then
        log_step "Invoking beril-adversarial-cli --type paper"
        local review_out="$draft_dir/reviews/draft_1_review_1.md"
        beril-adversarial-cli --type paper "$project_id" \
            > "$review_out" 2>&1 || \
            log_warn "beril-adversarial-cli exited non-zero; review may be partial"
        if [[ ! -s "$review_out" ]]; then
            log_warn "Adversarial review file empty; falling back to inline reviewer"
            run_fallback_reviewer "$project_root" "$draft_dir" "$model" || return 1
        fi
    else
        log_warn "beril-adversarial-cli not on PATH; using fallback inline reviewer"
        run_fallback_reviewer "$project_root" "$draft_dir" "$model" || return 1
    fi

    log_ok "review phase complete"
}

run_fallback_reviewer() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"

    local review_out="$draft_dir/reviews/draft_1_review_1.md"
    local user_prompt="Run the fallback adversarial reviewer against the assembled manuscript.

## Inputs

- \`MANUSCRIPT_PATH\` = \`$draft_dir/manuscript.md\`
- \`REVIEW_OUT_PATH\` = \`$review_out\`
- \`THROUGHLINE_PATH\` = \`$draft_dir/00_throughline.md\`
- \`METHODS_PROVENANCE_PATH\` = \`$draft_dir/methods_provenance.md\`
- \`REFRAMING_LOG_PATH\` = \`$draft_dir/reframing_log.md\`
- \`PROJECT_ROOT\` = \`$project_root\`

Read MANUSCRIPT_PATH; review per your system prompt's rubric; write REVIEW_OUT_PATH via Write; emit closing-message template."

    invoke_claude_with_retry \
        "$PROMPTS_DIR/fallback_reviewer.v1.md" "$user_prompt" "$model" \
        "$review_out" "fallback_reviewer.v1" "$draft_dir/audit/review.metadata.json"
}

# ==============================================================================
# Pause: emit handoff for review
# ==============================================================================

emit_review_handoff() {
    local draft_dir="$1"

    log_phase "Pause: review (final handoff)"

    local review_path
    review_path="$(ls "$draft_dir/reviews/"draft_*_review_*.md 2>/dev/null | head -1)"
    [[ -z "$review_path" ]] && review_path="$draft_dir/reviews/"

    "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" aggregate-metadata "$draft_dir" >/dev/null 2>&1

    # Emit next_actions.md with validator + reviewer + orphan-citation aggregate.
    "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" emit-next-actions "$draft_dir" \
        > "$draft_dir/audit/next_actions.log" 2>&1 \
        || log_warn "emit-next-actions failed; next_actions.md may be missing or incomplete"

    local prompt_msg="Manuscript drafted and reviewed. Final pause: read the manuscript at $draft_dir/manuscript.md and the review at $review_path. Before submission, work through $draft_dir/next_actions.md (validator failures + reviewer-flagged criticals + citation orphans aggregated into one checklist)."

    if ! "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" write-handoff "$draft_dir" \
            --phase review \
            --prompt-to-user "$prompt_msg" \
            --review-path "$review_path" \
            --resume-command "(MVP: rewrite loop not yet implemented; the manuscript at $draft_dir/manuscript.md is the final v0.1 deliverable; address $draft_dir/next_actions.md before submission)"; then
        log_error "write-handoff for review FAILED"
        log_error "  Slash-command parser cannot present the final review without a valid handoff."
        return 1
    fi

    set_state_phase "$draft_dir" "assembled"

    echo "" >&2
    echo "PAUSE: review (final)" >&2
    echo "  Manuscript: $draft_dir/manuscript.md" >&2
    echo "  Review:     $review_path" >&2
    echo "  Validation: $draft_dir/audit/validation.json" >&2
    echo "  Cost:       see $draft_dir/audit/run_metadata.json" >&2
}

# ==============================================================================
# Top-level dispatch
# ==============================================================================

# next_draft_dir <project_root> → echoes papers/draft_N/ with the next N.
next_draft_dir() {
    local project_root="$1"
    local papers_dir="$project_root/papers"
    mkdir -p "$papers_dir"
    local n=1
    while [[ -d "$papers_dir/draft_$n" ]]; do
        n=$((n + 1))
    done
    echo "$papers_dir/draft_$n"
}

# Resolve a project argument: accepts either a path or a project_id (looked
# up under projects/<id>/). Echoes the absolute project root.
resolve_project() {
    local arg="$1"
    if [[ -d "$arg" ]]; then
        cd -P "$arg" && pwd
    elif [[ -d "projects/$arg" ]]; then
        cd -P "projects/$arg" && pwd
    else
        log_error "Cannot resolve project: $arg"
        log_error "  Pass either an absolute path or a project_id under projects/."
        return 1
    fi
}

# Main: parse args, dispatch.
main() {
    init_skill_paths

    local verb="${1:-}"
    case "$verb" in
        ""|-h|--help)  usage; exit 0 ;;
        draft)         shift ;;
        resume)        shift ;;
        *)
            log_error "Unknown subcommand: $verb"
            usage
            exit 1
            ;;
    esac

    # Parse remaining args (positional + flags).
    local positional=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --model)          DEFAULT_MODEL="$2"; shift 2 ;;
            --depth)          PAPER_WRITER_DEPTH="$2"; export PAPER_WRITER_DEPTH; shift 2 ;;
            --mode)           PAPER_WRITER_MODE="$2"; export PAPER_WRITER_MODE; shift 2 ;;
            --no-stream)     NO_STREAM=1; shift ;;
            --no-adversarial) NO_ADVERSARIAL=1; shift ;;
            -*) log_error "Unknown flag: $1"; usage; exit 1 ;;
            *)
                if [[ -z "$positional" ]]; then
                    positional="$1"
                else
                    log_error "Unexpected extra arg: $1"; exit 1
                fi
                shift ;;
        esac
    done

    local model="${PAPER_WRITER_MODEL:-$DEFAULT_MODEL}"

    if [[ -z "$positional" ]]; then
        log_error "$verb requires a positional argument."
        usage
        exit 1
    fi

    case "$verb" in
        draft)
            local project_root
            project_root="$(resolve_project "$positional")" || exit 1
            local project_id
            project_id="$(basename "$project_root")"
            local draft_dir
            draft_dir="$(next_draft_dir "$project_root")"

            phase_init     "$draft_dir" "$project_id" || exit 2  # pre-mkdir; nothing to handoff into
            acquire_draft_lock "$draft_dir" "draft" || exit 1
            phase_extract  "$project_root" "$draft_dir" \
                || halt_with "$draft_dir" "extract phase failed (notebook AST parse or no notebooks at $project_root/notebooks/)"
            phase_plan     "$project_root" "$draft_dir" "$model" \
                || halt_with "$draft_dir" "plan.v1 failed after retry exhaustion; see audit/plan.metadata.json and (if preserved) .stream.log"
            emit_throughline_handoff "$draft_dir" \
                || halt_with "$draft_dir" "throughline_pick handoff emission failed (likely no parseable '## Candidate TLN:' headers in throughline_candidates.md)"

            echo "$draft_dir"  # final stdout: the draft_dir path for the caller
            ;;
        resume)
            local draft_dir
            if [[ -d "$positional" ]]; then
                draft_dir="$(cd -P "$positional" && pwd)"
            else
                log_error "draft_dir does not exist: $positional"
                exit 1
            fi

            acquire_draft_lock "$draft_dir" "resume" || exit 1

            local project_id
            project_id="$(read_state_field "$draft_dir" "project_id")"
            if [[ -z "$project_id" ]]; then
                halt_with "$draft_dir" \
                    "state.json missing project_id; cannot resume" \
                    "Manually inspect $draft_dir/state.json. If corrupted, start a fresh draft."
            fi

            # Resolve project_root from draft_dir layout: draft_dir is
            # typically <project_root>/papers/draft_N/, so go up two.
            local project_root
            project_root="$(cd -P "$draft_dir/../.." && pwd)"

            local current_phase
            current_phase="$(read_state_phase "$draft_dir")"
            log_phase "Resume from phase: $current_phase"

            case "$current_phase" in
                init)
                    # phase=init means a prior draft invocation halted before
                    # plan.v1 finished (e.g., claude not on PATH, retry
                    # exhausted). Re-run the early phases (idempotent — each
                    # checks output existence and skips). End state is
                    # phase=throughline_pick if plan.v1 succeeds this time.
                    log_step "Re-running init/extract/plan (idempotent)"
                    phase_init     "$draft_dir" "$project_id" || exit 2
                    phase_extract  "$project_root" "$draft_dir" \
                        || halt_with "$draft_dir" "extract phase failed during resume"
                    phase_plan     "$project_root" "$draft_dir" "$model" \
                        || halt_with "$draft_dir" "plan.v1 failed after retry exhaustion during resume"
                    emit_throughline_handoff "$draft_dir" \
                        || halt_with "$draft_dir" "throughline_pick handoff emission failed during resume"
                    ;;
                throughline_pick)
                    # NOT a halt: the existing throughline_pick handoff is
                    # already valid. Emit a stderr diagnostic and exit 1 so
                    # the slash-command parser knows to re-drive the pick UX
                    # rather than show a halted-state error.
                    log_error "phase=throughline_pick requires user pick first."
                    log_error "Use: beril-paper-writer continue $draft_dir --pick TLN [--revision text]"
                    log_error "(That command writes 00_throughline.md and sets phase=drafting before re-invoking resume.)"
                    log_error "Existing .handoff.json describes the pause; do not overwrite."
                    exit 1
                    ;;
                drafting)
                    if [[ ! -f "$draft_dir/00_throughline.md" ]]; then
                        halt_with "$draft_dir" \
                            "phase=drafting but 00_throughline.md is missing; state corrupted" \
                            "Either edit state.json's phase back to throughline_pick and re-run continue --pick, or start a fresh draft."
                    fi
                    phase_citation_pool "$project_root" "$draft_dir" "$model" \
                        || halt_with "$draft_dir" "citation_pool.v1 or formatter step failed; see audit/citation_pool_validate.log"
                    phase_methods       "$project_root" "$draft_dir" "$model" "$project_id" \
                        || halt_with "$draft_dir" "methods.v1 failed after retry exhaustion"
                    phase_results       "$project_root" "$draft_dir" "$model" \
                        || halt_with "$draft_dir" "results.v1 failed after retry exhaustion"
                    phase_discussion    "$project_root" "$draft_dir" "$model" \
                        || halt_with "$draft_dir" "discussion.v1 failed after retry exhaustion"
                    phase_intro         "$project_root" "$draft_dir" "$model" \
                        || halt_with "$draft_dir" "intro.v1 failed after retry exhaustion"
                    phase_abstract      "$project_root" "$draft_dir" "$model" \
                        || halt_with "$draft_dir" "abstract.v1 failed after retry exhaustion"
                    phase_reframe_drift_audit "$project_root" "$draft_dir" "$model" \
                        || halt_with "$draft_dir" "reframer.v1 drift audit failed; inspect audit/reframer.metadata.json"
                    phase_data_avail    "$project_root" "$draft_dir" "$project_id" \
                        || halt_with "$draft_dir" "data_availability template fill failed (orchestrator-side, no LLM); inspect tools/paper_writer_helpers.py"
                    phase_finalize_citations "$draft_dir" \
                        || halt_with "$draft_dir" "finalize_citations failed; inspect audit/finalize_citations.log"
                    phase_assemble      "$draft_dir" \
                        || halt_with "$draft_dir" "assemble (concat + validate_manuscript) failed"
                    set_state_phase     "$draft_dir" "review"
                    phase_review        "$project_root" "$draft_dir" "$model" "$project_id" \
                        || halt_with "$draft_dir" "review phase failed (adversarial-cli or fallback reviewer)"
                    emit_review_handoff "$draft_dir" \
                        || halt_with "$draft_dir" "review handoff emission failed"
                    ;;
                review)
                    # Re-run review phase only (idempotent).
                    phase_review        "$project_root" "$draft_dir" "$model" "$project_id" \
                        || halt_with "$draft_dir" "review phase failed during resume"
                    emit_review_handoff "$draft_dir" \
                        || halt_with "$draft_dir" "review handoff emission failed during resume"
                    ;;
                assembled)
                    log_step "Already assembled; nothing to do."
                    log_step "Manuscript at $draft_dir/manuscript.md"
                    ;;
                *)
                    halt_with "$draft_dir" \
                        "Unknown phase in state.json: $current_phase" \
                        "state.json may be corrupted or written by a future schema version."
                    ;;
            esac
            ;;
    esac
}

main "$@"
