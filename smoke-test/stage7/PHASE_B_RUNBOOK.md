# Phase B smoke-test runbook — [C-NNN] marker emission

**Filed:** 2026-05-18. **Status:** awaiting Adam's live run.
**Prereq:** Phase B-1..B-5 landed (prompt updated, validator built,
orchestrator wired, 23/23 unit tests pass, full suite 1092/1092).

## Purpose

Validate that `holistic_draft.v1.md`'s Stage 6 partial section
actually drives the LLM to emit `[C-NNN]` markers on numeric claims
at usable density, and that the new Tier-1 validator runs cleanly
in the orchestrator. The unit tests pin the validator's behaviour
against synthetic input; only a real `claude -p` call against a
real BERDL project tells us whether the prompt's discipline holds
under Opus.

## Gate to Phase C

The B-phase smoke proceeds to Phase C (dev runs against the
6-project set) iff **all five** of these are true:

1. `holistic_draft` writes manuscript.md (no crash; ≥1 paragraph
   per IMRAD section).
2. `check_claim_markers` runs as part of phase_review Tier 1 (the
   orchestrator log includes the `Stage 6 partial (claim markers):
   ...` summary line).
3. `audit/claim_marker_check.json` exists with schema
   `claim-marker-check.v1` and `inventory_size > 0`.
4. **Marker density on numeric claims is acceptable.** Of all
   numeric tokens in the manuscript that are NOT in the allowlist
   from Tier T (years, figure references, section numbers, etc.),
   at least ~60% should have a preceding/adjacent `[C-NNN]` marker.
   The check is qualitative — eyeball Methods + Results.
5. **Unresolved-marker rate is low.** `cited_but_unresolved` ≤ 5%
   of `markers_in_manuscript`. (A handful of fabricated markers is
   tolerable; pervasive fabrication indicates the prompt's
   discipline isn't holding and we need a B-phase iteration.)

Failure on (1) → drafter prompt has a hard bug; not Phase C.
Failure on (2) or (3) → orchestrator wiring bug; fix and retry.
Failure on (4) → density too low; tighten the prompt's "every
numeric claim" rule and rerun.
Failure on (5) → drafter is inventing markers; tighten the
"no inventory entry → don't make the claim" rule and rerun.

## Recommended target

`ibd_phage_targeting` as a fresh `draft_2` is the cheapest test —
the existing inventory is large (341 rows) so the marker space is
rich; the project's REPORT.md is the most mature of any BERDL
project; we have prior runs to compare against. Cost ballpark:
$4-8 for a full draft cycle pre-remediation.

Alternative: `phb_granule_ecology` if you want to also stress-test
on a project from the Stage 7 dev set; ecology shape is distinct
from microbiome-disease, may surface different prompt issues.

## Runbook

Run on your Mac (sandbox can't do live LLM calls).

```bash
# 0. Reinstall to pick up Phase B changes.
cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"
pipx install --force .

# 1. Verify Stage-6-partial code is in the installed binary.
# Expect: helper present, prompt mentions [C-NNN] markers.
beril-paper-writer continue --help 2>&1 | grep remediate >/dev/null && \
  echo "tier-S+B code present"

python3 - <<'PY'
import importlib.util, sys
spec = importlib.util.find_spec("beril_paper_writer.skill.tools.check_claim_markers")
print("check_claim_markers module present:", spec is not None)
PY

# 2. Pick the project. Default is ibd_phage_targeting.
PROJ="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/ibd_phage_targeting"

# 3. Fresh draft (pauses at throughline_pick).
beril-paper-writer draft "$PROJ"
# Note: the draft_dir printed at the end. Capture it:
DRAFT=$(ls -td "$PROJ/papers/draft_"*/ | head -1)
echo "draft_dir: $DRAFT"

# 4. Continue through to p0_review.
beril-paper-writer continue "$DRAFT" --pick TL1
```

## Inspect after the run

```bash
# What did the validator say?
cat "$DRAFT/audit/claim_marker_check.json" | python3 -m json.tool | head -40

# Quick density check on Methods + Results.
python3 - <<PY
import re, pathlib
m = pathlib.Path("$DRAFT/manuscript.md").read_text()
# All numeric tokens (regex matches Tier-T's class roughly).
numerics = re.findall(r"\b\d+(?:[.,]\d+)*\b", m)
markers = re.findall(r"\[(C-\d+)\]", m)
print(f"numerics in manuscript: {len(numerics)}")
print(f"unique markers:         {len(set(markers))}")
print(f"total marker tokens:    {len(markers)}")
# Crude density: ratio of markers to non-trivial numerics.
nontrivial = [n for n in numerics if len(n) >= 2 and n not in {"19","20","21"}]
print(f"non-trivial numerics:   {len(nontrivial)}")
if nontrivial:
    print(f"density (markers / non-trivial numerics): {len(markers)/len(nontrivial):.2f}")
PY

# Look at one Results paragraph to eyeball marker placement.
awk '/^## Results/{flag=1; next} /^## /{flag=0} flag' "$DRAFT/manuscript.md" \
    | head -30
```

## Recording the outcome

Append the result to `smoke-test/stage7/PHASE_B_RESULTS.md` (create
on first run). Pattern:

```markdown
## Run 2026-05-XX — ibd_phage_targeting/draft_N

- Cost:                $X.XX
- Wall clock:          X min
- Numerics:            N
- Unique markers:      N
- Density estimate:    N% (markers / non-trivial numerics)
- Unresolved markers:  N
- Gate verdict:        PASS / FAIL on which criteria
- Notes:               <observations>
```

## If gate fails

Most likely failure mode is (4) low density — the prompt added
"emit `[C-NNN]`" rules but the LLM doesn't follow them aggressively
enough. Iteration path:

1. Read the manuscript Methods + Results sections.
2. Find 3-5 numeric claims that SHOULD have markers but don't.
3. Pick the most common shape (e.g., "X of Y" patterns, percentages
   in parentheticals) — that's the prompt gap.
4. Edit `holistic_draft.v1.md` Stage-6-partial section's worked
   examples to include that shape. Add 1-2 more anti-patterns.
5. Re-run from step 3 of the runbook above.

Bound the B-phase iteration at 2 tries. If two prompt edits don't
move density into the 60% target, the v1 plan deferment of full
Stage 6 was the right call; ship Stage 6 partial without strict
density target and let the v1.1 reverse-direction check enforce.

## Cost ceiling for B-phase

~$30 total across draft + iteration retries. If you blow past
that, stop and re-evaluate — Stage 6 partial isn't worth more
than that for v1-MVP.
