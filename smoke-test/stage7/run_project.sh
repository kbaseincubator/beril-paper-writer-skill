#!/usr/bin/env bash
# Stage 7 v1-MVP validation harness — single-project driver.
#
# Drives beril-paper-writer on one BERDL project to the v1-MVP
# measurement point (no-auto-remediate, per Adam's 2026-05-19
# decision):
#   1. `beril-paper-writer draft <project>` → pause at throughline_pick
#   2. `beril-paper-writer continue <draft_dir> --pick TL1`
#      → pause at p0_review (the measurement point) OR assembled
#        (rare; only when zero P0s)
#   3. STOP. The operator decides whether to invoke remediation
#      themselves based on p0_findings.md; the harness does not.
#
# All output (stdout+stderr) is teed to a per-run log under runs/. Exit code:
#   0  reached p0_review OR assembled — measurement is meaningful
#   1  setup error (project not found, no CLI on PATH, etc.)
#   2  pipeline hard-fail (LLM error, crash, etc.)
#
# Usage:
#   ./run_project.sh <project_id> [<beril_extended_dir>]
#
# Examples:
#   ./run_project.sh conservation_vs_fitness
#   ./run_project.sh metal_specificity /custom/path/to/beril-extended
#
# Idempotency: each invocation creates a fresh papers/draft_N/ for the project.
# If you want a clean re-run, manually delete the latest papers/draft_N/.

set -euo pipefail

# ---------------------------------------------------------------------------
# Args + paths
# ---------------------------------------------------------------------------

PROJECT_ID="${1:?usage: run_project.sh <project_id> [<beril_extended_dir>]}"

# Default BERIL-extended root — Adam's workspace layout. Overridable.
BERIL_EXTENDED="${2:-$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended}"

PROJECT_DIR="$BERIL_EXTENDED/projects/$PROJECT_ID"

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "error: project not found at $PROJECT_DIR" >&2
  exit 1
fi
if [[ ! -f "$PROJECT_DIR/REPORT.md" ]]; then
  echo "error: $PROJECT_DIR has no REPORT.md (ineligible)" >&2
  exit 1
fi

# Verify the CLI is on PATH and has Tier-S flags (so we know we're on v0.8+).
if ! command -v beril-paper-writer >/dev/null 2>&1; then
  echo "error: beril-paper-writer not on PATH" >&2
  exit 1
fi
# Capture `continue --help` into a variable FIRST, then test the captured
# string. Do NOT pipe directly into `grep -q`: under `set -o pipefail`,
# `grep -q` closes the pipe on its first match (here `--remediate` is on
# line 5 of ~56), the Python producer still holding unwritten lines gets
# SIGPIPE / BrokenPipeError and exits non-zero, and pipefail propagates
# that as the pipeline's exit code — a false "pre-Tier-S" verdict even
# when `--help` plainly contains `--remediate`. It's a race: when Python
# flushes its whole buffer before grep closes, the preflight passes; when
# grep wins the race, it false-fails. Command substitution drains ALL
# output, so the producer never SIGPIPEs. (Diagnosed 2026-05-20 when H2
# of the Stage 7 holdout campaign false-failed after H1+H3 passed.)
BPW_RESOLVED="$(command -v beril-paper-writer)"
BPW_CONTINUE_HELP="$(beril-paper-writer continue --help 2>&1 || true)"
if [[ -z "$BPW_CONTINUE_HELP" ]]; then
  echo "error: 'beril-paper-writer continue --help' produced no output" >&2
  echo "  resolved binary: $BPW_RESOLVED" >&2
  echo "  (binary crashed, or PATH resolved a broken install)" >&2
  exit 1
fi
# Bash glob membership test — no external grep, no pipe, no SIGPIPE risk.
if [[ "$BPW_CONTINUE_HELP" != *"--remediate"* ]]; then
  echo "error: installed beril-paper-writer is pre-Tier-S (no --remediate flag)" >&2
  echo "  resolved binary: $BPW_RESOLVED" >&2
  exit 1
fi

# Per-run logging.
HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNS_DIR="$HARNESS_DIR/runs"
mkdir -p "$RUNS_DIR"
TS=$(date -u +%Y-%m-%dT%H-%M-%SZ)
LOG="$RUNS_DIR/${PROJECT_ID}_${TS}.log"

# Everything from here on tees to LOG.
exec > >(tee -a "$LOG") 2>&1

echo "==================================================================="
echo "Stage 7 v1-MVP validation harness"
echo "project_id:       $PROJECT_ID"
echo "project_dir:      $PROJECT_DIR"
echo "log:              $LOG"
echo "started:          $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "==================================================================="

START_TS=$(date +%s)

# ---------------------------------------------------------------------------
# Step 1: draft (runs through plan; pauses at throughline_pick)
# ---------------------------------------------------------------------------

echo
echo "--- step 1: beril-paper-writer draft $PROJECT_DIR ---"

# Note (2026-05-18): draft.py prints the draft_dir on stdout ONLY when
# run_pipeline propagates PipelineHalted to the outer except block.
# Empirically (conservation_vs_fitness/draft_1, 2026-05-18 02:09 UTC),
# orchestrator.run_pipeline catches PipelineHalted internally (line 483)
# so it never propagates and print(draft_dir) is dead code on the
# happy path. We auto-discover instead — robust against the
# pre-existing contract bug (filed as task #24, fix in v1.x).
if ! beril-paper-writer draft "$PROJECT_DIR"; then
  RC=$?
  if [[ $RC -ne 0 ]]; then
    echo "error: beril-paper-writer draft exited $RC" >&2
    # Don't hard-fail here — auto-discovery below may still find a
    # draft_dir on disk (the orchestrator wrote state before raising).
  fi
fi

# Auto-discover the latest draft_N directory. ls -td sorts directories
# by mtime descending; head -1 picks the most recent. The `|| true`
# guards the same pipefail+SIGPIPE class as the preflight above: `head`
# closes the pipe early, `ls` can SIGPIPE. ls output here is tiny so the
# race is near-impossible, but `|| true` makes it provably safe under
# `set -euo pipefail`; the `-z` check below catches a genuinely empty
# result either way.
DRAFT_DIR=$(ls -td "$PROJECT_DIR/papers/draft_"*/ 2>/dev/null | head -1 || true)
DRAFT_DIR="${DRAFT_DIR%/}"   # strip trailing slash from ls -d format
if [[ -z "$DRAFT_DIR" || ! -d "$DRAFT_DIR" ]]; then
  echo "error: no papers/draft_N/ found under $PROJECT_DIR" >&2
  exit 2
fi
echo
echo "draft_dir: $DRAFT_DIR"

# Sanity: confirm state.json is at throughline_pick (the expected halt
# point of step 1). If not, log loudly but proceed — step 2 will
# either advance or fail with a useful error.
if [[ -f "$DRAFT_DIR/state.json" ]]; then
  PHASE=$(python3 -c "import json,pathlib,sys; print(json.loads(pathlib.Path('$DRAFT_DIR/state.json').read_text()).get('phase','unknown'))")
  echo "phase after step 1: $PHASE"
  if [[ "$PHASE" != "throughline_pick" ]]; then
    echo "warning: expected throughline_pick, got $PHASE" >&2
  fi
fi

# ---------------------------------------------------------------------------
# Step 2: continue --pick TL1 (runs through review; pauses at p0_review)
# ---------------------------------------------------------------------------

echo
echo "--- step 2: beril-paper-writer continue $DRAFT_DIR --pick TL1 ---"

if ! beril-paper-writer continue "$DRAFT_DIR" --pick TL1; then
  RC=$?
  # Exit 0 expected here too — pause at p0_review is the success path.
  # Non-zero means something blew up (e.g., LLM error during citation_pool).
  echo "error: continue --pick TL1 exited $RC" >&2
  exit 2
fi

# Inspect state.json — what phase did we end at?
CURRENT_PHASE=$(python3 -c "
import json, sys, pathlib
p = pathlib.Path('$DRAFT_DIR/state.json')
print(json.loads(p.read_text()).get('phase', 'unknown'))
")
echo
echo "phase after step 2: $CURRENT_PHASE"

# v1-MVP measurement model (locked 2026-05-19): the harness ALWAYS
# pauses at first p0_review. Adversarial-reviewer sampling variance
# (Stage 7 dev runs forensic, V1_X_BACKLOG.md #37: 40-80% stable
# core + 1-5 new findings per run; D3 case found 3 entirely new P0s
# after the drafter changed 1 word in 71KB of manuscript) means
# auto-remediation introduces noise on already-good drafts. The
# operator decides whether to remediate per project — invoking
# `beril-paper-writer continue <draft_dir> --remediate` themselves
# when the p0_findings.md content warrants.
#
# Phase D verdict is based on the first-cut quality at the p0_review
# pause: did the drafter produce an operator-shippable starting point?
# Bar is enforced in collect_metrics.py.
#
# Possible terminal phases here:
#   p0_review  — gate paused; this is the v1-MVP measurement point
#   assembled  — no P0s, pipeline auto-finished (rare); also a pass
#   anything else — unexpected; flag for diagnosis
if [[ "$CURRENT_PHASE" == "assembled" ]]; then
  echo
  echo "note: pipeline finished without any P0s — no remediation needed."
elif [[ "$CURRENT_PHASE" == "p0_review" ]]; then
  echo
  echo "Pipeline paused at p0_review (the v1-MVP measurement point)."
  echo "To run operator-driven remediation, invoke:"
  echo "  beril-paper-writer continue $DRAFT_DIR --remediate --max-remediate-cycles N"
else
  echo
  echo "warning: continue --pick TL1 left phase at $CURRENT_PHASE (expected p0_review or assembled)"
  echo "this is unusual; flagged in metrics collection"
fi

# ---------------------------------------------------------------------------
# Step 4: emit final state + handoff
# ---------------------------------------------------------------------------

END_TS=$(date +%s)
RUNTIME=$((END_TS - START_TS))

FINAL_PHASE=$(python3 -c "
import json, pathlib
p = pathlib.Path('$DRAFT_DIR/state.json')
print(json.loads(p.read_text()).get('phase', 'unknown'))
")

echo
echo "==================================================================="
echo "Stage 7 harness — run complete"
echo "  project_id:    $PROJECT_ID"
echo "  draft_dir:     $DRAFT_DIR"
echo "  final_phase:   $FINAL_PHASE"
echo "  runtime_s:     $RUNTIME"
echo "  log:           $LOG"
echo "==================================================================="

# Single line on stdout (after exec tee), parseable by aggregate.py:
echo "STAGE7_RESULT project=$PROJECT_ID draft_dir=$DRAFT_DIR phase=$FINAL_PHASE runtime_s=$RUNTIME"

exit 0
