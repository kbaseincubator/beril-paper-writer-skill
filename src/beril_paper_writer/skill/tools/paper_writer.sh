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
    --max-cost-usd <N> Halt with handoff if cumulative LLM spend exceeds N USD
                       (checked before each LLM call; default: no cap)
    --recaption        Force re-synthesis of LLM figure captions (default: skip
                       figures with existing audit/figure_caption_<N>.md files)
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

# Cost circuit-breaker (Item 5.2). Empty = no cap. Set by --max-cost-usd flag.
# Checked inside invoke_claude_with_retry before each LLM call.
MAX_COST_USD=""
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
#
# Item 5.2: enforces --max-cost-usd circuit breaker before each call.
# Halts via halt_with if cumulative cost across audit/*.metadata.json
# already exceeds the cap; the next call would only push further over.
invoke_claude_with_retry() {
    local sys_prompt_file="$1"
    local base_prompt="$2"
    local model="$3"
    local expected_path="$4"
    local label="$5"
    local metadata_path="$6"

    # Cost circuit-breaker (Item 5.2). Active only when --max-cost-usd
    # was set. Derive draft_dir from the metadata_path's parent's parent
    # (metadata lives at <draft>/audit/<label>.metadata.json).
    if [[ -n "$MAX_COST_USD" ]]; then
        local draft_dir
        draft_dir="$(cd "$(dirname "$metadata_path")/.." 2>/dev/null && pwd)"
        if [[ -n "$draft_dir" && -d "$draft_dir" ]]; then
            local cumulative
            cumulative=$("$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" \
                cumulative-cost "$draft_dir" 2>/dev/null || echo "0.0000")
            local over
            over=$(awk -v c="$cumulative" -v m="$MAX_COST_USD" \
                'BEGIN{print (c+0 > m+0) ? "1" : "0"}')
            if [[ "$over" == "1" ]]; then
                log_error "Cost circuit-breaker tripped before $label"
                log_error "  cumulative spend: \$${cumulative}  >  --max-cost-usd cap: \$${MAX_COST_USD}"
                log_error "  Next call would push further over. Halting via handoff."
                halt_with "$draft_dir" \
                    "Cost circuit-breaker tripped: cumulative \$${cumulative} > cap \$${MAX_COST_USD}" \
                    "Re-run with a higher --max-cost-usd to continue, or accept the partial draft as-is and address next_actions.md before submission."
            fi
        fi
    fi

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
# Phase: extract — run extract_methods.py + extract_figures.py + extract_tables.py
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

    if [[ -f "$draft_dir/tables_inventory.md" ]]; then
        log_step "tables_inventory.md exists, skipping extract_tables.py"
    else
        log_step "Running extract_tables.py"
        "$PYTHON_BIN" "$TOOLS_DIR/extract_tables.py" "$project_root" \
            --output-dir "$draft_dir" 2>&1 | tee -a "$draft_dir/audit/extract_tables.log" \
            || log_warn "extract_tables.py exited non-zero; tables inventory may be empty (not a hard failure)."
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

# Parse glyph counts from the evidence map (✓ and ⚠ symbols) to build
# a one-line summary for the AskUserQuestion picker.
def _summarize_glyphs(block: str) -> str:
    direct = block.count("✓")
    partial = block.count("⚠")
    unsup = block.count("✗")
    parts = []
    if direct:
        parts.append(f"{direct} ✓ direct")
    if partial:
        parts.append(f"{partial} ⚠ partial")
    if unsup:
        parts.append(f"{unsup} ✗ unsupported")
    return "; ".join(parts) if parts else ""

choices = []
# Split into per-candidate blocks for glyph counting
blocks = re.split(r"(?=^## Candidate TL\d+:)", text, flags=re.MULTILINE)
for block in blocks:
    m = re.match(r"^## Candidate (TL\d+):\s*(.+)$", block, re.MULTILINE)
    if not m:
        continue
    cid = m.group(1)
    label = m.group(2).strip()
    if len(label) > 100:
        label = label[:97] + "..."
    glyph_summary = _summarize_glyphs(block)
    # One-line description for the picker: label + glyph summary
    picker_desc = label
    if glyph_summary:
        picker_desc = f"{label} ({glyph_summary})"
    if len(picker_desc) > 120:
        picker_desc = picker_desc[:117] + "..."
    choices.append({
        "id": cid,
        "label": label,
        "picker_description": picker_desc,
    })
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

    # v0.3 Tier 2.2 — stale-file cleanup. Drafting-mode entry only (we're
    # not in REPAIR_MODE here; that's a separate dispatch path). Removes
    # any pre-existing fig*.png paper-order files left over from a prior
    # rewrite-loop run; results.v1 will re-copy fresh files based on its
    # current selection. Documented in DECISIONS.md (D-NN-figure-cleanup).
    if [[ -d "$draft_dir/figures" ]]; then
        local stale_count
        stale_count=$(find "$draft_dir/figures" -maxdepth 1 -type f -name 'fig*.png' 2>/dev/null | wc -l)
        if [[ "$stale_count" -gt 0 ]]; then
            log_step "Removing $stale_count stale paper-order figure(s) from $draft_dir/figures/"
            rm -f "$draft_dir/figures/"fig*.png
        fi
    else
        mkdir -p "$draft_dir/figures"
    fi

    local figures_inventory="$draft_dir/figures_inventory.md"
    [[ ! -f "$figures_inventory" ]] && figures_inventory=""

    local tables_inventory="$draft_dir/tables_inventory.md"
    [[ ! -f "$tables_inventory" ]] && tables_inventory=""

    local mode tier
    mode="$(read_state_field "$draft_dir" "mode")"; [[ -z "$mode" ]] && mode="paper"
    tier="$(read_state_field "$draft_dir" "tier")"; [[ -z "$tier" ]] && tier="STRONG"

    local figures_clause=""
    if [[ -n "$figures_inventory" ]]; then
        figures_clause="
- \`FIGURES_INVENTORY_PATH\` = \`$figures_inventory\`"
    fi

    local tables_clause=""
    if [[ -n "$tables_inventory" ]]; then
        tables_clause="
- \`TABLES_INVENTORY_PATH\` = \`$tables_inventory\`"
    fi

    local user_prompt
    user_prompt="Run results.v1 to produce $target.

## Inputs

- \`PROJECT_ROOT\` = \`$project_root\`
- \`DRAFT_DIR\` = \`$draft_dir\`
- \`FIGURES_OUT_DIR\` = \`$draft_dir/figures\`
- \`RESULTS_OUT_PATH\` = \`$target\`
- \`THROUGHLINE_PATH\` = \`$draft_dir/00_throughline.md\`
- \`METHODS_PATH\` = \`$draft_dir/01_methods.md\`
- \`METHODS_PROVENANCE_PATH\` = \`$draft_dir/methods_provenance.md\`
- \`REPORT_PATH\` = \`$project_root/REPORT.md\`
- \`RESEARCH_PLAN_PATH\` = \`$project_root/RESEARCH_PLAN.md\`
- \`REFRAMING_LOG_PATH\` = \`$draft_dir/reframing_log.md\`$figures_clause$tables_clause
- \`MODE\` = \`$mode\`
- \`TIER\` = \`$tier\`

Write RESULTS_OUT_PATH via the Write tool. Emit closing-message template naming the K selected figures and T selected tables."

    invoke_claude_with_retry \
        "$PROMPTS_DIR/results.v1.md" "$user_prompt" "$model" \
        "$target" "results.v1" "$draft_dir/audit/results.metadata.json" \
        || return 1

    # v0.4 Phase 0: legacy filename-grep fallback removed. In v0.1/v0.2
    # results.v1 prose contained raw filenames; that fallback grepped them
    # and copied as a backstop. v0.3+ prose uses (Fig. N) callouts and
    # results.v1 owns figure-copy directly via its Bash tool, so the
    # fallback always reported "Copied 0 figure(s)" and was misleading.
    # If a future regression reintroduces filename-style callouts, that's
    # a separate fix; the manifest existence check in
    # tools/check_figures_manifest.py is the authoritative dangling-reference
    # detector now.

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

    log_phase "Phase: data_availability (orchestrator-side template fill + extraction)"

    local target="$draft_dir/07_data_availability.md"
    if [[ -f "$target" ]]; then
        log_step "07_data_availability.md exists, skipping fill"
        return 0
    fi

    local pw_version
    pw_version="$("$PYTHON_BIN" -c 'from beril_paper_writer import __version__; print(__version__)' 2>/dev/null || echo '0.1.0')"

    # v0.2: extract K-BERDL databases + public accessions from project
    # artifacts; falls back to [TBD] markers if extraction surfaces nothing
    # (defensive — never blocks the pipeline).
    local extraction_json="$draft_dir/audit/data_availability_extraction.json"
    "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" extract-data-availability \
        "$draft_dir" --project-root "$project_root" \
        > "$extraction_json" 2> "$draft_dir/audit/data_availability_extraction.log" \
        || log_warn "extract-data-availability exited non-zero; falling back to [TBD] markers"

    # Pull the three blocks out of the JSON for fill-template. Use
    # --<key>-json file-passing to avoid bash word-splitting hazards
    # on multi-line block content.
    local kberdl_block_path="$draft_dir/audit/.data_avail_kberdl.txt"
    local public_block_path="$draft_dir/audit/.data_avail_public.txt"
    local restricted_block_path="$draft_dir/audit/.data_avail_restricted.txt"

    "$PYTHON_BIN" - <<PYEOF "$extraction_json" "$kberdl_block_path" "$public_block_path" "$restricted_block_path"
import json, sys
src, kp, pp, rp = sys.argv[1:]
try:
    with open(src) as f:
        d = json.load(f)
    kberdl = d.get("kberdl_databases_block", "")
    public = d.get("public_accessions_block", "")
    restricted = d.get("restricted_access_block", "")
    diag = d.get("diagnostics", {})
    print(
        f"[data-availability] {diag.get('n_kberdl_databases', 0)} K-BERDL database(s); "
        f"{diag.get('n_named_sources', 0)} named source(s); "
        f"{diag.get('n_typed_accessions', 0)} typed accession(s)",
        file=sys.stderr,
    )
except Exception as e:
    print(f"[data-availability] extraction JSON parse failed: {e}; using TBD fallback", file=sys.stderr)
    kberdl = "[K-BERDL DATABASES: TBD — extraction failed; review methods_provenance.md and fill manually before submission.]"
    public = "[PUBLIC ACCESSIONS: TBD — extraction failed; review RESEARCH_PLAN.md and fill manually before submission.]"
    restricted = "[RESTRICTED ACCESS: TBD — extraction failed; default assumption is 'all publicly available' but confirm before submission.]"
with open(kp, "w", encoding="utf-8") as f:
    f.write(kberdl)
with open(pp, "w", encoding="utf-8") as f:
    f.write(public)
with open(rp, "w", encoding="utf-8") as f:
    f.write(restricted)
PYEOF

    local kberdl_block public_block restricted_block
    kberdl_block="$(cat "$kberdl_block_path")"
    public_block="$(cat "$public_block_path")"
    restricted_block="$(cat "$restricted_block_path")"

    "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" fill-template \
        "$REFERENCES_DIR/data_availability_template.md" \
        "$target" \
        --var code_repo_url="[CODE REPO: TBD — fill before submission]" \
        --var code_repo_ref="HEAD" \
        --var paper_writer_version="$pw_version" \
        --var kberdl_databases_block="$kberdl_block" \
        --var public_accessions_block="$public_block" \
        --var restricted_access_block="$restricted_block" \
        --var requirements_file_path="requirements.txt" \
        > /dev/null

    # Cleanup tempfiles on success.
    rm -f "$kberdl_block_path" "$public_block_path" "$restricted_block_path"

    log_ok "data_avail phase complete (BERDL-specific extraction landed v0.2)"
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
# Phase: check_scope_coherence — Discussion↔Results scope cross-walk (advisory)
# ==============================================================================
#
# Sits between finalize_citations and assemble. Mirrors the
# check_throughline_glyphs.py architectural pattern (post-processor that
# emits stderr WARN, always exits 0; orchestrator surfaces in
# next_actions.md). Reads 00_throughline.md, 02_results.md, and
# 03_discussion.md from $draft_dir; writes warnings to
# audit/scope_warnings.txt.

phase_check_scope_coherence() {
    local draft_dir="$1"

    log_phase "Phase: check_scope_coherence (advisory cross-walk)"

    local scope_warnings_file="$draft_dir/audit/scope_warnings.txt"
    "$PYTHON_BIN" "$TOOLS_DIR/check_scope_coherence.py" "$draft_dir" \
        2> "$scope_warnings_file" || true

    if grep -q "^\[check_scope_coherence\] WARN" "$scope_warnings_file"; then
        local n_warn
        n_warn=$(grep -c "^\[check_scope_coherence\] WARN" "$scope_warnings_file")
        log_warn "Scope-coherence cross-walk: $n_warn warning(s) (will surface in next_actions.md):"
        grep "^\[check_scope_coherence\] WARN" "$scope_warnings_file" | head -5 | sed 's/^/  /' >&2
        if [[ "$n_warn" -gt 5 ]]; then
            echo "  ... (see $scope_warnings_file for the full list)" >&2
        fi
    fi

    log_ok "check_scope_coherence phase complete"
}

# ==============================================================================
# Phase: check_overclaim — Abstract/Discussion strong-claim cross-walk (advisory)
# ==============================================================================
#
# Same hook point as check_scope_coherence (between finalize_citations and
# assemble). Mirrors the architectural pattern: post-processor that emits
# stderr WARN, always exits 0; orchestrator surfaces in next_actions.md.

phase_check_overclaim() {
    local draft_dir="$1"

    log_phase "Phase: check_overclaim (advisory cross-walk)"

    local overclaim_warnings_file="$draft_dir/audit/overclaim_warnings.txt"
    "$PYTHON_BIN" "$TOOLS_DIR/check_overclaim.py" "$draft_dir" \
        2> "$overclaim_warnings_file" || true

    if grep -q "^\[check_overclaim\] WARN" "$overclaim_warnings_file"; then
        local n_warn
        n_warn=$(grep -c "^\[check_overclaim\] WARN" "$overclaim_warnings_file")
        log_warn "Overclaim cross-walk: $n_warn warning(s) (will surface in next_actions.md):"
        grep "^\[check_overclaim\] WARN" "$overclaim_warnings_file" | head -5 | sed 's/^/  /' >&2
        if [[ "$n_warn" -gt 5 ]]; then
            echo "  ... (see $overclaim_warnings_file for the full list)" >&2
        fi
    fi

    log_ok "check_overclaim phase complete"
}

# ==============================================================================
# Phase: check_figures_manifest — figures_manifest.tsv cross-walk (advisory)
# ==============================================================================
#
# v0.3 Tier 2.1b. Sits before assemble (and, when 2.2 lands, before
# phase_embed_figures). Mirrors the post-processor architectural pattern.
# Validates the figures_manifest.tsv contract from the artifact side:
# schema, filesystem agreement, and (Fig. N) callout cross-walk.
# Always exits 0; warnings surface in next_actions.md.

phase_check_figures_manifest() {
    local draft_dir="$1"

    log_phase "Phase: check_figures_manifest (advisory cross-walk)"

    local fig_warnings_file="$draft_dir/audit/figures_manifest_warnings.txt"
    "$PYTHON_BIN" "$TOOLS_DIR/check_figures_manifest.py" "$draft_dir" \
        2> "$fig_warnings_file" || true

    if grep -q "^\[check_figures_manifest\] WARN" "$fig_warnings_file"; then
        local n_warn
        n_warn=$(grep -c "^\[check_figures_manifest\] WARN" "$fig_warnings_file")
        log_warn "Figures-manifest cross-walk: $n_warn warning(s) (will surface in next_actions.md):"
        grep "^\[check_figures_manifest\] WARN" "$fig_warnings_file" | head -5 | sed 's/^/  /' >&2
        if [[ "$n_warn" -gt 5 ]]; then
            echo "  ... (see $fig_warnings_file for the full list)" >&2
        fi
    fi

    log_ok "check_figures_manifest phase complete"
}

# ==============================================================================
# Phase: caption_synthesis — Source 4 LLM for figures failing the gate
# ==============================================================================
#
# v0.4 Phase 4c. Runs after phase_check_figures_manifest, before
# phase_embed_figures. For each figure in the manifest:
#   1. Build the input bundle from descriptor + REPORT prose +
#      Results-section prose + prose-side panel callouts.
#   2. Apply the sufficiency gate: if Sources 2+3 yielded notebook prose
#      ≥30 words AND title-or-axes populated, mark source_chosen=
#      "deterministic" and skip Source 4.
#   3. Otherwise invoke figure_caption.v1.md with the bundle inlined in
#      the user_prompt; capture the LLM-written caption at
#      audit/figure_caption_<N>.md; compute stats and update
#      audit/figure_caption.v1.metadata.json.
#
# Idempotent for the deterministic side; LLM invocations re-run on every
# call (orchestrator-level deduplication is a v0.5 candidate via
# --recaption flag). Cost-circuit-breaker (--max-cost-usd) applies via
# invoke_claude_with_retry.

phase_caption_synthesis() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"

    log_phase "Phase: caption_synthesis (Source 4 LLM for sparse-descriptor figures)"

    local bundles_dir="$draft_dir/audit/caption_bundles"
    local meta_path="$draft_dir/audit/figure_caption.v1.metadata.json"

    # Build bundles + apply sufficiency gate. stdout is the list of
    # figure_ids that need Source 4; empty stdout means all pass.
    local fig_ids
    fig_ids=$("$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" \
        build-caption-bundles \
        --draft-dir "$draft_dir" \
        --project-root "$project_root" \
        --bundles-dir "$bundles_dir") || true

    if [[ -z "$fig_ids" ]]; then
        log_step "All figures pass the sufficiency gate; skipping Source 4."
        log_ok "caption_synthesis phase complete (0 LLM invocations)"
        return 0
    fi

    local n_figs
    n_figs=$(echo "$fig_ids" | wc -l | tr -d ' ')
    log_step "Source 4 needed for $n_figs figure(s): $(echo "$fig_ids" | tr '\n' ' ')"

    local n_synth=0
    while IFS= read -r fig_id; do
        [[ -z "$fig_id" ]] && continue
        local bundle_path="$bundles_dir/figure_${fig_id}.bundle.json"
        local output_path="$draft_dir/audit/figure_caption_${fig_id}.md"
        local label="figure_caption.v1[fig=$fig_id]"
        local cap_metadata="$draft_dir/audit/figure_caption_${fig_id}.invoke.metadata.json"

        if [[ ! -f "$bundle_path" ]]; then
            log_warn "Bundle file missing for figure $fig_id at $bundle_path; skipping."
            continue
        fi

        # v0.6 backlog 0c: skip if caption already synthesized (idempotency).
        # Re-running the pipeline should not re-invoke LLM for figures
        # whose captions already exist. Use --recaption flag to force.
        if [[ -f "$output_path" && "${RECAPTION:-}" != "true" ]]; then
            log_step "Caption exists for figure $fig_id at $output_path; skipping (use --recaption to force)."
            continue
        fi

        log_step "Synthesizing caption for figure $fig_id"

        # Read the bundle JSON into a variable for inlining; per the
        # prompt's contract, all inputs flow through user_prompt (no
        # Read-tool input gathering).
        local bundle_json
        bundle_json=$(cat "$bundle_path")

        local user_prompt
        user_prompt="Run figure_caption.v1 to produce $output_path.

## Inputs (JSON bundle)

\`\`\`json
$bundle_json
\`\`\`

## Output path

Write the caption markdown to \`$output_path\` via the Write tool.

After Write succeeds, emit the closing-message template (see prompt §Closing-message)."

        invoke_claude_with_retry \
            "$PROMPTS_DIR/figure_caption.v1.md" "$user_prompt" "$model" \
            "$output_path" "$label" "$cap_metadata" \
            || {
                log_warn "figure_caption.v1 failed for figure $fig_id; falling back to deterministic description."
                continue
            }

        # Update metadata.json with stats from the written caption.
        "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" \
            compute-caption-stats \
            --draft-dir "$draft_dir" \
            --figure-id "$fig_id" >&2 || true

        n_synth=$((n_synth + 1))
    done <<< "$fig_ids"

    log_ok "caption_synthesis phase complete ($n_synth/$n_figs LLM-synthesized)"
}

# ==============================================================================
# Phase: check_caption_provenance — Source 4 fabrication detector
# ==============================================================================
#
# v0.4 Phase 4c. Runs after phase_caption_synthesis, before
# phase_embed_figures. Sixth post-checker in the v0.2-pattern lineage.
# Validates LLM-synthesized captions (source_chosen='llm' entries in
# audit/figure_caption.v1.metadata.json) against their input bundles:
# numerical claims, named entities, panel letters, word counts.

phase_check_caption_provenance() {
    local draft_dir="$1"

    log_phase "Phase: check_caption_provenance (advisory)"

    local prov_warnings_file="$draft_dir/audit/caption_provenance_warnings.txt"
    "$PYTHON_BIN" "$TOOLS_DIR/check_caption_provenance.py" "$draft_dir" \
        2> "$prov_warnings_file" || true

    if grep -q "^\[check_caption_provenance\] WARN" "$prov_warnings_file"; then
        local n_warn
        n_warn=$(grep -c "^\[check_caption_provenance\] WARN" "$prov_warnings_file")
        log_warn "Caption-provenance check: $n_warn warning(s) (will surface in next_actions.md):"
        grep "^\[check_caption_provenance\] WARN" "$prov_warnings_file" | head -5 | sed 's/^/  /' >&2
        if [[ "$n_warn" -gt 5 ]]; then
            echo "  ... (see $prov_warnings_file for the full list)" >&2
        fi
    fi

    log_ok "check_caption_provenance phase complete"
}

# ==============================================================================
# Phase: embed_figures — inject ![Figure N: caption](path) tags after callouts
# ==============================================================================
#
# v0.3 Tier 2.2. Runs after phase_check_figures_manifest (which validates
# the manifest contract from results.v1) and before phase_assemble. Walks
# 02_results.md / 01_methods.md / 03_discussion.md for (Fig. N) callouts;
# for each first-occurrence per N, injects an markdown image tag of the
# form `![Figure N: <caption>](figures/<filename>)` after the sentence
# containing the callout. Idempotent (re-running does not double-inject);
# safe to invoke from rewrite-loop re-assembly paths.

phase_embed_figures() {
    local draft_dir="$1"

    log_phase "Phase: embed_figures (inject markdown image tags)"

    local embed_log="$draft_dir/audit/embed_figures.log"
    "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" embed-figures "$draft_dir" \
        > "$embed_log" 2>&1 || true

    # Surface the stdout summary line + count any WARNs.
    if [[ -f "$embed_log" ]]; then
        local summary
        summary=$(grep -E '^embedded: ' "$embed_log" | tail -1)
        [[ -n "$summary" ]] && log_step "$summary"
        local n_warn
        n_warn=$(grep -c '^WARN:' "$embed_log" || true)
        if [[ "$n_warn" -gt 0 ]]; then
            log_warn "embed_figures emitted $n_warn WARN(s) (see $embed_log):"
            grep '^WARN:' "$embed_log" | head -3 | sed 's/^/  /' >&2
        fi
    fi

    log_ok "embed_figures phase complete"
}

# ==============================================================================
# Phase: check_tables_manifest — tables_manifest.tsv cross-walk (advisory)
# ==============================================================================
#
# v0.6 Tier 9. Mirrors phase_check_figures_manifest. Validates the
# tables_manifest.tsv contract from the artifact side: schema,
# tables_inventory.md cross-reference, (Table N) callout cross-walk,
# wide-table warning, and duplicate detection. Always exits 0; warnings
# surface in next_actions.md.

phase_check_tables_manifest() {
    local draft_dir="$1"

    log_phase "Phase: check_tables_manifest (advisory cross-walk)"

    local tbl_warnings_file="$draft_dir/audit/tables_manifest_warnings.txt"
    "$PYTHON_BIN" "$TOOLS_DIR/check_tables_manifest.py" "$draft_dir" \
        --inventory "$draft_dir/tables_inventory.md" \
        2> "$tbl_warnings_file" || true

    if grep -q "^\[check_tables_manifest\] WARN" "$tbl_warnings_file"; then
        local n_warn
        n_warn=$(grep -c "^\[check_tables_manifest\] WARN" "$tbl_warnings_file")
        log_warn "Tables-manifest cross-walk: $n_warn warning(s) (will surface in next_actions.md):"
        grep "^\[check_tables_manifest\] WARN" "$tbl_warnings_file" | head -5 | sed 's/^/  /' >&2
        if [[ "$n_warn" -gt 5 ]]; then
            echo "  ... (see $tbl_warnings_file for the full list)" >&2
        fi
    fi

    log_ok "check_tables_manifest phase complete"
}

# ==============================================================================
# Phase: embed_tables — inject **Table N.** caption + markdown table after callouts
# ==============================================================================
#
# v0.6 Tier 9. Mirrors phase_embed_figures. Walks 02_results.md /
# 01_methods.md / 03_discussion.md for (Table N) callouts; for each
# first-occurrence per N, injects a formatted **Table N.** block with
# the caption and markdown table content from tables_inventory.md.
# Idempotent (re-running does not double-inject); safe to invoke from
# rewrite-loop re-assembly paths.

phase_embed_tables() {
    local draft_dir="$1"

    log_phase "Phase: embed_tables (inject table blocks)"

    local embed_log="$draft_dir/audit/embed_tables.log"
    "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" embed-tables "$draft_dir" \
        > "$embed_log" 2>&1 || true

    # Surface the stdout summary line + count any WARNs.
    if [[ -f "$embed_log" ]]; then
        local summary
        summary=$(grep -E '^embedded: ' "$embed_log" | tail -1)
        [[ -n "$summary" ]] && log_step "$summary"
        local n_warn
        n_warn=$(grep -c '^WARN:' "$embed_log" || true)
        if [[ "$n_warn" -gt 0 ]]; then
            log_warn "embed_tables emitted $n_warn WARN(s) (see $embed_log):"
            grep '^WARN:' "$embed_log" | head -3 | sed 's/^/  /' >&2
        fi
    fi

    log_ok "embed_tables phase complete"
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
        log_warn "validate_manuscript.py reported failures (see $draft_dir/audit/validation.json); deferring to phase_repair_validators"

    # Cleanup transient title-block tempfile (manuscript.md has the title).
    rm -f "$title_block_path"

    log_ok "assemble phase complete: $manuscript"
}

# ==============================================================================
# Phase: repair_validators — auto-fix M2-M10 failures via REPAIR_MODE dispatch
# ==============================================================================
#
# Runs after phase_assemble (which writes audit/validation.json). For each
# fail validator, dispatches the section prompt in REPAIR_MODE per the
# LAYOUT.md:419 dispatch table. Bounded retry: 2 dispatches per validator.
# After each dispatch, re-runs validate_manuscript.py and checks whether
# the named validator now passes. If exhausted, surfaces escalation in
# audit/repair_summary.txt (which emit-next-actions surfaces in
# next_actions.md).
#
# Special-case validators per LAYOUT:
#   - M1 (missing IMRAD section): escalate as user-modify; orchestrator's
#     missing-section redraft path is not implemented in v0.1.
#   - M4 (data availability): escalate as user-modify; template missing.
#
# Side effects per validator:
#   - audit/repair_<VID>_input.json  (single-validator filtered failure)
#   - audit/repair_<VID>_pre.md      (pre-repair snapshot for post-checker)
#   - audit/repair_<VID>_attempt<N>.metadata.json  (claude-side cost sidecar)
#   - audit/repair_<VID>_post_validation.json  (after re-validation)
#   - audit/repair_summary.txt  (per-validator outcome lines)

phase_repair_validators() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"
    local project_id="$4"

    log_phase "Phase: repair_validators (auto-fix M-validator failures via REPAIR_MODE)"

    local validation_path="$draft_dir/audit/validation.json"
    if [[ ! -f "$validation_path" ]]; then
        log_warn "validation.json missing; skipping repair phase"
        return 0
    fi

    local repair_summary="$draft_dir/audit/repair_summary.txt"
    : > "$repair_summary"

    local fail_ids
    fail_ids=$("$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" \
        list-failed-validators "$validation_path")

    if [[ -z "$fail_ids" ]]; then
        log_step "No validator failures; nothing to repair"
        echo "no validator failures at phase entry; nothing dispatched" >> "$repair_summary"
        log_ok "repair_validators phase complete (no-op)"
        return 0
    fi

    log_step "Failed validators to repair: $(echo $fail_ids | tr '\n' ' ')"
    echo "Failed validators at phase entry: $(echo $fail_ids | tr '\n' ' ')" >> "$repair_summary"

    local vid
    while IFS= read -r vid; do
        [[ -z "$vid" ]] && continue
        repair_one_validator "$project_root" "$draft_dir" "$model" "$project_id" "$vid" \
            >> "$repair_summary" 2>&1 || true
    done <<< "$fail_ids"

    log_ok "repair_validators phase complete (see $repair_summary)"
}

# Resolve the canonical-source input block (per-section drafting-mode inputs)
# as a heredoc-friendly string. Centralized to avoid per-validator drift.
_emit_repair_inputs_block() {
    local project_root="$1"
    local draft_dir="$2"
    local target_var_name="$3"
    local target_path="$4"
    local validator_output_path="$5"
    local vid="$6"
    local mode tier
    mode="$(read_state_field "$draft_dir" "mode")"; [[ -z "$mode" ]] && mode="paper"
    tier="$(read_state_field "$draft_dir" "tier")"; [[ -z "$tier" ]] && tier="STRONG"

    cat <<EOF
## Inputs (canonical sources — pass-through; read what your section prompt needs)

- \`PROJECT_ROOT\` = \`$project_root\`
- \`DRAFT_DIR\` = \`$draft_dir\`
- \`THROUGHLINE_PATH\` = \`$draft_dir/00_throughline.md\`
- \`REPORT_PATH\` = \`$project_root/REPORT.md\`
- \`RESEARCH_PLAN_PATH\` = \`$project_root/RESEARCH_PLAN.md\`
- \`METHODS_PATH\` = \`$draft_dir/01_methods.md\`
- \`RESULTS_PATH\` = \`$draft_dir/02_results.md\`
- \`DISCUSSION_PATH\` = \`$draft_dir/03_discussion.md\`
- \`INTRODUCTION_PATH\` = \`$draft_dir/04_introduction.md\`
- \`ABSTRACT_PATH\` = \`$draft_dir/05_abstract.md\`
- \`POOL_JSON_PATH\` = \`$draft_dir/pool.json\`
- \`REFERENCES_MD_PATH\` = \`$draft_dir/references.md\`
- \`REFRAMING_LOG_PATH\` = \`$draft_dir/reframing_log.md\`
- \`METHODS_PROVENANCE_PATH\` = \`$draft_dir/methods_provenance.md\`
- \`FIGURES_INVENTORY_PATH\` = \`$draft_dir/figures_inventory.md\`
- \`MODE\` = \`$mode\`
- \`TIER\` = \`$tier\`

## REPAIR_MODE inputs (the four required for repair semantics)

- \`REPAIR_MODE\` = \`true\`
- \`NAMED_VALIDATOR\` = \`$vid\`
- \`VALIDATOR_OUTPUT_PATH\` = \`$validator_output_path\` (filtered to the single \`$vid\` failure; read this for the violation detail)
- \`REPAIR_TARGET_PATH\` = \`$target_path\` (the file you must rewrite via the Write tool)

Read VALIDATOR_OUTPUT_PATH for the structured failure detail; identify the named span in REPAIR_TARGET_PATH; fix only that span; do not regenerate the section; do not introduce new claims; do not delete grounded claims that other validators did not flag. Re-write REPAIR_TARGET_PATH via the Write tool, then emit your prompt's REPAIR_MODE closing message.
EOF
}

# Repair one named validator. Bounded retry: 2 dispatches per validator.
# All outcome lines go to stdout (caller redirects to repair_summary.txt).
repair_one_validator() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"
    local project_id="$4"
    local vid="$5"

    log_step "Repairing $vid"

    # Resolve dispatch via Python helper (eval-safe key=value lines).
    local dispatch_stdout
    dispatch_stdout=$("$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" \
        prepare-repair "$draft_dir" --validator "$vid")

    local SECTION_PROMPT="" TARGET_FILENAME="" TARGET_VAR_NAME=""
    local TARGET_PATH="" VALIDATOR_OUTPUT_PATH=""
    local DISPATCH_STATUS="" ESCALATION_NOTE="" VIOLATIONS_COUNT=0

    # Parse `KEY=value` lines without invoking eval (which would expand any
    # backticks / $-vars in the values — a real risk for ESCALATION_NOTE).
    local line
    while IFS= read -r line; do
        local key="${line%%=*}"
        local val="${line#*=}"
        case "$key" in
            SECTION_PROMPT)         SECTION_PROMPT="$val" ;;
            TARGET_FILENAME)        TARGET_FILENAME="$val" ;;
            TARGET_VAR_NAME)        TARGET_VAR_NAME="$val" ;;
            TARGET_PATH)            TARGET_PATH="$val" ;;
            VALIDATOR_OUTPUT_PATH)  VALIDATOR_OUTPUT_PATH="$val" ;;
            VIOLATIONS_COUNT)       VIOLATIONS_COUNT="$val" ;;
            DISPATCH_STATUS)        DISPATCH_STATUS="$val" ;;
            ESCALATION_NOTE)        ESCALATION_NOTE="$val" ;;
        esac
    done <<< "$dispatch_stdout"

    case "$DISPATCH_STATUS" in
        skip)
            log_step "$vid: skipping ($ESCALATION_NOTE)"
            echo "$vid: skip — $ESCALATION_NOTE"
            return 0
            ;;
        escalate)
            log_warn "$vid: escalating per LAYOUT — $ESCALATION_NOTE"
            echo "$vid: escalate — $ESCALATION_NOTE"
            return 0
            ;;
        ready)
            ;;
        *)
            log_warn "$vid: unknown DISPATCH_STATUS '$DISPATCH_STATUS'; skipping"
            echo "$vid: unknown dispatch status: $DISPATCH_STATUS"
            return 0
            ;;
    esac

    log_step "$vid: dispatching to $SECTION_PROMPT (target=$TARGET_FILENAME, $VIOLATIONS_COUNT violation(s))"

    # Snapshot pre-repair file for the Item 3.4 post-checker.
    local pre_snapshot="$draft_dir/audit/repair_${vid}_pre.md"
    cp "$TARGET_PATH" "$pre_snapshot"

    # Build the user prompt once (same input set across attempts).
    local user_prompt
    user_prompt="REPAIR_MODE invocation: address the $vid validator failure in $TARGET_FILENAME.

$(_emit_repair_inputs_block "$project_root" "$draft_dir" "$TARGET_VAR_NAME" "$TARGET_PATH" "$VALIDATOR_OUTPUT_PATH" "$vid")"

    # Bounded retry: 2 orchestrator-level dispatches per validator. Each
    # dispatch passes through invoke_claude_with_retry's stochastic-failure
    # retries (3 attempts on Write-not-invoked) and the prompt's internal
    # 2-attempt repair semantics.
    local mode
    mode="$(read_state_field "$draft_dir" "mode")"; [[ -z "$mode" ]] && mode="paper"

    local attempt
    for attempt in 1 2; do
        log_step "$vid: dispatch $attempt/2"

        local metadata_path="$draft_dir/audit/repair_${vid}_attempt${attempt}.metadata.json"

        invoke_claude_with_retry \
            "$PROMPTS_DIR/$SECTION_PROMPT" "$user_prompt" "$model" \
            "$TARGET_PATH" "repair_$vid" "$metadata_path"
        local rc=$?

        if [[ $rc -ne 0 ]]; then
            log_warn "$vid: dispatch $attempt/2 invocation failed (rc=$rc); aborting repair for this validator"
            echo "$vid: invocation-fail on attempt $attempt (rc=$rc); user-modify recommended"
            return 0
        fi

        # Re-run the full validator to confirm the fix landed.
        local post_validation="$draft_dir/audit/repair_${vid}_post_validation.json"
        "$PYTHON_BIN" "$TOOLS_DIR/validate_manuscript.py" "$draft_dir" \
            --mode "$mode" --output "$post_validation" \
            > "$draft_dir/audit/repair_${vid}_post_validate_attempt${attempt}.log" 2>&1 || true

        # Check whether the named validator now passes.
        local status_stdout
        status_stdout=$("$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" \
            check-repair-status "$post_validation" --validator "$vid")
        local v_status=""
        local sline
        while IFS= read -r sline; do
            [[ "$sline" == STATUS=* ]] && v_status="${sline#STATUS=}"
        done <<< "$status_stdout"

        if [[ "$v_status" == "pass" ]]; then
            log_ok "$vid: dispatch $attempt/2 fixed the failure ✓"
            echo "$vid: repaired on attempt $attempt; dispatched to $SECTION_PROMPT"

            # Run check_repair_scope post-checker (Item 3.4) if available.
            if [[ -f "$TOOLS_DIR/check_repair_scope.py" ]]; then
                local scope_log="$draft_dir/audit/repair_${vid}_scope_warnings.txt"
                "$PYTHON_BIN" "$TOOLS_DIR/check_repair_scope.py" \
                    --pre "$pre_snapshot" --post "$TARGET_PATH" \
                    --validator "$vid" --draft-dir "$draft_dir" \
                    2> "$scope_log" || true
                if grep -q "^\[check_repair_scope\] WARN" "$scope_log" 2>/dev/null; then
                    local n_scope
                    n_scope=$(grep -c "^\[check_repair_scope\] WARN" "$scope_log")
                    log_warn "$vid: post-repair scope checker emitted $n_scope warning(s)"
                    echo "$vid: scope-check warnings ($n_scope) — see $scope_log"
                fi
            fi

            return 0
        else
            log_warn "$vid: dispatch $attempt/2 did NOT fix the failure (status=$v_status); will retry if attempts remain"
            echo "$vid: post-attempt $attempt status=$v_status; retrying" >&2
        fi
    done

    log_warn "$vid: 2 dispatches exhausted; surfacing escalation"
    echo "$vid: 2 dispatches exhausted; user-modify recommended"
    return 0
}

# ==============================================================================
# Phase: review — single-pass adversarial OR fallback
# ==============================================================================

phase_review() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"
    local project_id="$4"

    log_phase "Phase: review (initial pass; rewrite loop runs after if criticals)"

    # Idempotency: skip if review_1 already exists.
    if [[ -f "$draft_dir/reviews/draft_1_review_1.md" ]]; then
        log_step "draft_1_review_1.md exists; skipping initial review pass"
        return 0
    fi

    run_reviewer_pass "$project_root" "$draft_dir" "$model" "$project_id" 1 || return 1

    log_ok "review phase complete"
}

# run_reviewer_pass <project_root> <draft_dir> <model> <project_id> <review_number>
# Writes to $draft_dir/reviews/draft_1_review_${review_number}.md.
# Reused by phase_review (number=1) and phase_review_rewrite (number=2,3).
run_reviewer_pass() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"
    local project_id="$4"
    local review_number="$5"

    local review_out="$draft_dir/reviews/draft_1_review_${review_number}.md"
    if [[ -f "$review_out" ]]; then
        log_step "Review pass $review_number already exists; skipping"
        return 0
    fi

    if [[ "${NO_ADVERSARIAL:-0}" == "1" ]]; then
        log_step "--no-adversarial set; using fallback inline reviewer (pass $review_number)"
        run_fallback_reviewer "$project_root" "$draft_dir" "$model" "$review_out" "$review_number" || return 1
        return 0
    fi

    if command -v beril-adversarial-cli &>/dev/null; then
        log_step "Invoking beril-adversarial-cli --type paper (pass $review_number)"
        beril-adversarial-cli --type paper "$project_id" \
            > "$review_out" 2>&1 || \
            log_warn "beril-adversarial-cli exited non-zero on pass $review_number; review may be partial"
        if [[ ! -s "$review_out" ]]; then
            log_warn "Adversarial review file empty; falling back to inline reviewer"
            run_fallback_reviewer "$project_root" "$draft_dir" "$model" "$review_out" "$review_number" || return 1
        fi
    else
        log_warn "beril-adversarial-cli not on PATH; using fallback inline reviewer (pass $review_number)"
        run_fallback_reviewer "$project_root" "$draft_dir" "$model" "$review_out" "$review_number" || return 1
    fi
}

run_fallback_reviewer() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"
    local review_out="${4:-$draft_dir/reviews/draft_1_review_1.md}"
    local review_number="${5:-1}"

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
        "$review_out" "fallback_reviewer.v1" \
        "$draft_dir/audit/review_pass${review_number}.metadata.json"
}

# ==============================================================================
# Phase: review_rewrite — bounded retry loop applying review findings via rewrite.v1
# ==============================================================================
#
# After phase_review produces draft_1_review_1.md, this phase:
#   1. Counts Critical findings; if zero, no-op return.
#   2. Pass 1: parse review with min_severity=important (Critical+Important);
#      dispatch rewrite.v1 per affected section; re-assemble (re-validates);
#      run reviewer pass 2 (writes draft_1_review_2.md).
#   3. Pass 2 (only if Critical persists in pass-2 review): parse with
#      min_severity=critical; dispatch rewrite.v1 with REWRITE_PASS_NUMBER=2
#      and the prompt's pass-2 discipline; re-assemble; run reviewer pass 3.
#   4. Per SPEC §8.3 hard cap of 2 rewrite passes: any criticals remaining
#      after pass 2 are surfaced in next_actions.md, not re-rewritten.
#
# Side effects:
#   audit/rewrite_summary.txt          — per-pass dispatch + outcome lines
#   audit/rewrite_pass<N>_<section>.metadata.json — claude-side cost sidecar
#   reviews/draft_1_review_<2|3>.md    — post-rewrite review files
#   manuscript.md, audit/validation.json — re-rendered after each rewrite

phase_review_rewrite() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"
    local project_id="$4"

    log_phase "Phase: review_rewrite (bounded loop applying Critical+Important review findings)"

    local review_path="$draft_dir/reviews/draft_1_review_1.md"
    if [[ ! -f "$review_path" ]]; then
        log_warn "No review file at $review_path; skipping rewrite loop"
        return 0
    fi

    local rewrite_summary="$draft_dir/audit/rewrite_summary.txt"
    : > "$rewrite_summary"

    local n_crit
    n_crit=$("$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" \
        count-review-criticals "$review_path")
    [[ -z "$n_crit" ]] && n_crit=0

    if [[ "$n_crit" -eq 0 ]]; then
        log_step "Reviewer flagged 0 Critical findings; rewrite loop skipped"
        echo "pass 0: 0 Critical findings in $review_path; rewrite loop skipped" >> "$rewrite_summary"
        log_ok "review_rewrite phase complete (no-op)"
        return 0
    fi

    log_step "Initial review has $n_crit Critical finding(s); entering rewrite loop"
    echo "pass 0: initial review at $review_path has $n_crit Critical finding(s)" >> "$rewrite_summary"

    local pass_num
    for pass_num in 1 2; do
        local min_sev
        if [[ "$pass_num" -eq 1 ]]; then
            min_sev="important"
        else
            min_sev="critical"
        fi

        log_step "Rewrite pass $pass_num (min_severity=$min_sev) parsing $review_path"

        local parsed_json="$draft_dir/audit/rewrite_pass${pass_num}_findings.json"
        "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" \
            parse-review "$review_path" --min-severity "$min_sev" \
            > "$parsed_json"

        # Walk findings_by_section; for each section, dispatch rewrite.v1.
        local sections
        sections=$("$PYTHON_BIN" -c "
import json, sys
d = json.load(open('$parsed_json'))
for k in d.get('findings_by_section', {}).keys():
    print(k)
")
        if [[ -z "$sections" ]]; then
            log_step "Pass $pass_num: no findings at min_severity=$min_sev; loop terminates"
            echo "pass $pass_num: no findings at min_severity=$min_sev; loop terminates" >> "$rewrite_summary"
            break
        fi

        local sec_key
        while IFS= read -r sec_key; do
            [[ -z "$sec_key" ]] && continue
            dispatch_rewrite_for_section \
                "$project_root" "$draft_dir" "$model" "$project_id" \
                "$pass_num" "$min_sev" "$review_path" "$parsed_json" "$sec_key" \
                >> "$rewrite_summary" 2>&1 || true
        done <<< "$sections"

        # Re-assemble manuscript + re-run validators (rewrite changed sections).
        # phase_embed_figures and phase_embed_tables are idempotent — re-running
        # after a rewrite-loop rewrite of 02_results.md / 01_methods.md /
        # 03_discussion.md ensures any new (Fig. N) or (Table N) callouts get
        # embedded; already-embedded items are not double-injected.
        log_step "Pass $pass_num: re-embedding figures + tables + re-assembling manuscript"
        phase_embed_figures "$draft_dir" || \
            log_warn "Pass $pass_num re-embed figures failed; continuing"
        phase_embed_tables "$draft_dir" || \
            log_warn "Pass $pass_num re-embed tables failed; continuing"
        phase_assemble "$draft_dir" || \
            log_warn "Pass $pass_num re-assemble failed; continuing"

        # Run a fresh reviewer pass on the rewritten manuscript.
        # v0.2.1 fix: force-delete the target file BEFORE calling
        # run_reviewer_pass. The function has an idempotency check that
        # skips if the file exists; on a draft being resumed (e.g., after
        # the user re-runs the pipeline), an old review_N.md from a prior
        # rewrite cycle would silently make pass N operate against stale
        # findings. Deleting first forces a fresh review of the post-
        # rewrite manuscript. The initial review_1.md (in phase_review)
        # keeps its idempotency — only the rewrite loop forces fresh.
        local next_review_num=$((pass_num + 1))
        local next_review_path="$draft_dir/reviews/draft_1_review_${next_review_num}.md"
        rm -f "$next_review_path"
        log_step "Pass $pass_num: invoking reviewer pass $next_review_num (forced fresh)"
        run_reviewer_pass "$project_root" "$draft_dir" "$model" "$project_id" "$next_review_num" || {
            log_warn "Reviewer pass $next_review_num failed; surfacing escalation"
            echo "pass $pass_num: reviewer pass $next_review_num invocation failed; loop aborted" >> "$rewrite_summary"
            break
        }

        review_path="$draft_dir/reviews/draft_1_review_${next_review_num}.md"
        n_crit=$("$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" \
            count-review-criticals "$review_path")
        [[ -z "$n_crit" ]] && n_crit=0

        echo "pass $pass_num complete: post-rewrite review at $review_path has $n_crit Critical finding(s)" >> "$rewrite_summary"

        if [[ "$n_crit" -eq 0 ]]; then
            log_ok "Pass $pass_num: Critical findings cleared after rewrite ✓"
            return 0
        fi

        log_warn "Pass $pass_num: $n_crit Critical finding(s) remain"
    done

    # SPEC §8.3 hard cap reached.
    if [[ "$n_crit" -gt 0 ]]; then
        log_warn "Hard cap of 2 rewrite passes reached; $n_crit Critical finding(s) remain — surfacing in next_actions.md"
        echo "hard cap reached: $n_crit Critical finding(s) remain after 2 rewrite passes; user-modify recommended (per SPEC §8.3, fold into Limitations / Next Steps if persistent)" >> "$rewrite_summary"
    fi

    log_ok "review_rewrite phase complete (see $rewrite_summary)"
}

# Dispatch rewrite.v1 for one section. All outcome lines go to stdout
# (caller redirects to rewrite_summary.txt).
dispatch_rewrite_for_section() {
    local project_root="$1"
    local draft_dir="$2"
    local model="$3"
    local project_id="$4"
    local pass_num="$5"
    local min_sev="$6"
    local review_path="$7"
    local parsed_json="$8"
    local sec_key="$9"

    # Look up FINDING_IDS + section file via Python.
    local lookup_stdout
    lookup_stdout=$("$PYTHON_BIN" -c "
import json
d = json.load(open('$parsed_json'))
sec_file = d.get('section_files', {}).get('$sec_key', '')
fids = [f['id'] for f in d.get('findings_by_section', {}).get('$sec_key', [])]
print('FILE=' + sec_file)
print('IDS=' + ','.join(fids))
")
    local sec_file=""
    local finding_ids=""
    local line
    while IFS= read -r line; do
        case "$line" in
            FILE=*) sec_file="${line#FILE=}" ;;
            IDS=*)  finding_ids="${line#IDS=}" ;;
        esac
    done <<< "$lookup_stdout"

    if [[ -z "$sec_file" || -z "$finding_ids" ]]; then
        log_warn "[$sec_key] no section file or finding ids resolved; skipping"
        echo "pass $pass_num [$sec_key]: skip — no section/findings resolved"
        return 0
    fi

    local section_path="$draft_dir/$sec_file"
    if [[ ! -f "$section_path" ]]; then
        log_warn "[$sec_key] section file missing at $section_path; skipping"
        echo "pass $pass_num [$sec_key]: skip — $section_path not found"
        return 0
    fi

    # Severity for the prompt's MIN_SEVERITY input — capitalized form.
    local min_sev_capitalized
    if [[ "$min_sev" == "important" ]]; then
        min_sev_capitalized="Important"
    elif [[ "$min_sev" == "critical" ]]; then
        min_sev_capitalized="Critical"
    else
        min_sev_capitalized="Suggested"
    fi

    # Build FINDING_IDS as a JSON array literal for the prompt.
    local finding_ids_json
    finding_ids_json=$("$PYTHON_BIN" -c "
import json, sys
ids = '$finding_ids'.split(',') if '$finding_ids' else []
print(json.dumps(ids))
")

    local mode tier
    mode="$(read_state_field "$draft_dir" "mode")"; [[ -z "$mode" ]] && mode="paper"
    tier="$(read_state_field "$draft_dir" "tier")"; [[ -z "$tier" ]] && tier="STRONG"

    log_step "Pass $pass_num [$sec_key]: dispatching rewrite.v1 for findings $finding_ids → $sec_file"

    # Snapshot pre-rewrite for diagnostic.
    local pre_snapshot="$draft_dir/audit/rewrite_pass${pass_num}_${sec_key}_pre.md"
    cp "$section_path" "$pre_snapshot"

    local user_prompt="Run rewrite.v1 to apply review findings ${finding_ids_json} to ${sec_file} on rewrite pass ${pass_num} of 2 (per SPEC §8.3).

## Inputs (rewrite.v1 contract)

- \`PROJECT_ROOT\` = \`$project_root\`
- \`DRAFT_DIR\` = \`$draft_dir\`
- \`SECTION_PATH\` = \`$section_path\`
- \`REVIEW_PATH\` = \`$review_path\`
- \`FINDING_IDS\` = ${finding_ids_json}
- \`MIN_SEVERITY\` = \`${min_sev_capitalized}\`
- \`REWRITE_PASS_NUMBER\` = \`${pass_num}\`
- \`REFRAMING_LOG_PATH\` = \`$draft_dir/reframing_log.md\`
- \`MODE\` = \`$mode\`
- \`TIER\` = \`$tier\`

## Canonical sources (rewrite.v1 reads what it needs to verify fix viability)

- \`THROUGHLINE_PATH\` = \`$draft_dir/00_throughline.md\`
- \`REPORT_PATH\` = \`$project_root/REPORT.md\`
- \`METHODS_PATH\` = \`$draft_dir/01_methods.md\`
- \`RESULTS_PATH\` = \`$draft_dir/02_results.md\`
- \`DISCUSSION_PATH\` = \`$draft_dir/03_discussion.md\`
- \`INTRODUCTION_PATH\` = \`$draft_dir/04_introduction.md\`
- \`ABSTRACT_PATH\` = \`$draft_dir/05_abstract.md\`
- \`POOL_JSON_PATH\` = \`$draft_dir/pool.json\`
- \`REFERENCES_MD_PATH\` = \`$draft_dir/references.md\`
- \`METHODS_PROVENANCE_PATH\` = \`$draft_dir/methods_provenance.md\`

Apply the listed findings per rewrite.v1's discipline (minimal scoped edits; reframing-log entry per finding; cross-finding consistency check; cascade abandonment per pass-2 strictness if applicable). Re-write SECTION_PATH via the Write tool; emit closing-message template."

    local metadata_path="$draft_dir/audit/rewrite_pass${pass_num}_${sec_key}.metadata.json"

    invoke_claude_with_retry \
        "$PROMPTS_DIR/rewrite.v1.md" "$user_prompt" "$model" \
        "$section_path" "rewrite_pass${pass_num}_${sec_key}" "$metadata_path"
    local rc=$?

    if [[ $rc -ne 0 ]]; then
        log_warn "Pass $pass_num [$sec_key]: rewrite.v1 invocation failed (rc=$rc); section unchanged"
        echo "pass $pass_num [$sec_key]: invocation-fail (rc=$rc); user-modify recommended"
        return 0
    fi

    log_ok "Pass $pass_num [$sec_key]: rewrite.v1 completed for $sec_file"
    echo "pass $pass_num [$sec_key]: rewritten $sec_file (findings ${finding_ids_json})"
    return 0
}

# ==============================================================================
# Phase: assemble_docx — markdown → docx via python-docx
# ==============================================================================

phase_assemble_docx() {
    local draft_dir="$1"

    log_phase "Phase: assemble_docx (markdown → docx)"

    local manuscript="$draft_dir/manuscript.md"
    local output_docx="$draft_dir/manuscript.docx"

    if [[ ! -f "$manuscript" ]]; then
        log_warn "manuscript.md missing; skipping docx generation"
        return 0
    fi

    if "$PYTHON_BIN" "$TOOLS_DIR/assemble_docx.py" "$manuscript" "$output_docx" 2>"$draft_dir/audit/assemble_docx.log"; then
        log_ok "docx generated: $output_docx"
    else
        local rc=$?
        log_warn "assemble_docx.py exited $rc; docx may be missing or incomplete (see $draft_dir/audit/assemble_docx.log)"
        # Non-fatal: the pipeline continues without a docx.
        return 0
    fi
}

# ==============================================================================
# Pause: emit handoff for review
# ==============================================================================

emit_review_handoff() {
    local draft_dir="$1"

    log_phase "Pause: review (final handoff)"

    # v0.2.1 fix: pick the LATEST review by numeric suffix, not alphabetic.
    # `ls | head -1` picked draft_1_review_1.md even when review_2 / _3
    # existed, surfacing a stale review file in the final handoff. Use a
    # Python one-liner so we don't depend on `sort -V` (GNU coreutils).
    local review_path
    review_path="$("$PYTHON_BIN" - "$draft_dir/reviews" <<'PYEOF' 2>/dev/null
import os, re, sys
d = sys.argv[1]
if not os.path.isdir(d):
    sys.exit(0)
files = []
for f in os.listdir(d):
    m = re.match(r"draft_\d+_review_(\d+)\.md$", f)
    if m:
        files.append((int(m.group(1)), os.path.join(d, f)))
files.sort()
if files:
    print(files[-1][1])
PYEOF
)"
    [[ -z "$review_path" ]] && review_path="$draft_dir/reviews/"

    "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" aggregate-metadata "$draft_dir" >/dev/null 2>&1

    # Emit next_actions.md with validator + reviewer + orphan-citation aggregate.
    "$PYTHON_BIN" "$TOOLS_DIR/paper_writer_helpers.py" emit-next-actions "$draft_dir" \
        > "$draft_dir/audit/next_actions.log" 2>&1 \
        || log_warn "emit-next-actions failed; next_actions.md may be missing or incomplete"

    local docx_note=""
    if [[ -f "$draft_dir/manuscript.docx" ]]; then
        docx_note=" The docx is at $draft_dir/manuscript.docx."
    fi
    local prompt_msg="Manuscript drafted and reviewed. Final pause: read the manuscript at $draft_dir/manuscript.md and the review at $review_path.${docx_note} Before submission, work through $draft_dir/next_actions.md (validator failures + reviewer-flagged criticals + citation orphans aggregated into one checklist)."

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
    [[ -f "$draft_dir/manuscript.docx" ]] && echo "  Docx:       $draft_dir/manuscript.docx" >&2
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
            --max-cost-usd)   MAX_COST_USD="$2"; shift 2 ;;
            --recaption)      RECAPTION="true"; export RECAPTION; shift ;;
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

                    # Ensure directories exist (phase_init only runs on draft verb).
                    mkdir -p "$draft_dir/audit" "$draft_dir/figures" "$draft_dir/reviews"

                    # Ensure extract outputs exist (extract phase only runs on
                    # draft verb or init resume; a reset-to-drafting resume
                    # skips it). Each sub-extractor is idempotent (skips if
                    # its output file already exists).
                    if [[ ! -f "$draft_dir/methods_provenance.md" ]] \
                       || [[ ! -f "$draft_dir/figures_inventory.md" ]] \
                       || [[ ! -f "$draft_dir/tables_inventory.md" ]]; then
                        log_step "One or more extract outputs missing; re-running extract phase (idempotent)"
                        phase_extract "$project_root" "$draft_dir" \
                            || halt_with "$draft_dir" "extract phase failed during resume"
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
                    phase_check_figures_manifest "$draft_dir"
                    phase_caption_synthesis "$project_root" "$draft_dir" "$model"
                    phase_check_caption_provenance "$draft_dir"
                    phase_embed_figures "$draft_dir"
                    phase_check_tables_manifest "$draft_dir"
                    phase_embed_tables "$draft_dir"
                    phase_check_scope_coherence "$draft_dir"
                    phase_check_overclaim "$draft_dir"
                    phase_assemble      "$draft_dir" \
                        || halt_with "$draft_dir" "assemble (concat + validate_manuscript) failed"
                    phase_repair_validators "$project_root" "$draft_dir" "$model" "$project_id"
                    set_state_phase     "$draft_dir" "review"
                    phase_review        "$project_root" "$draft_dir" "$model" "$project_id" \
                        || halt_with "$draft_dir" "review phase failed (adversarial-cli or fallback reviewer)"
                    phase_review_rewrite "$project_root" "$draft_dir" "$model" "$project_id"
                    phase_assemble_docx "$draft_dir"
                    emit_review_handoff "$draft_dir" \
                        || halt_with "$draft_dir" "review handoff emission failed"
                    ;;
                review)
                    # Ensure directories exist (phase_init only runs on draft verb).
                    mkdir -p "$draft_dir/audit" "$draft_dir/figures" "$draft_dir/reviews"

                    # Re-run review phase only (idempotent — review_1 is skipped if present).
                    # Then run the rewrite loop in case the user wants to resume from a previously
                    # interrupted rewrite cycle. Both phases are no-ops when their output exists.
                    phase_review        "$project_root" "$draft_dir" "$model" "$project_id" \
                        || halt_with "$draft_dir" "review phase failed during resume"
                    phase_review_rewrite "$project_root" "$draft_dir" "$model" "$project_id"
                    phase_assemble_docx "$draft_dir"
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
