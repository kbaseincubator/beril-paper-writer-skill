"""orchestrator.py — Pure Python orchestrator for the paper writing pipeline.

This replaces the monolithic bash script with a native Python state machine
that uses `asyncio` for concurrent drafting and provides robust logging for
traceability.
"""

import os
import sys
import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Optional

from beril_paper_writer.config import config

from beril_paper_writer.state import DraftState, VALID_PHASES, load_state, save_state

# Robust logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ"
)
logger = logging.getLogger("orchestrator")


class TokenLimitExceeded(Exception):
    """Raised when the cumulative LLM spend exceeds the max_cost_usd cap."""
    pass


class PipelineHalted(Exception):
    """Raised when the pipeline pauses intentionally (e.g., for handoffs)."""
    pass


class DiscrepancyInteractiveHalt(Exception):
    """Raised when severe discrepancies require user interaction."""
    pass


def resolve_claude_bin() -> str:
    """Resolve the `claude` CLI to an absolute path (Stage 3 Tier J).

    The orchestrator spawns `claude -p` via asyncio.create_subprocess_exec.
    Passing the bare name "claude" relies on a PATH lookup at spawn time,
    which is fragile: observed 2026-05-12, a backgrounded run under Claude
    Code's Bash tool raised `FileNotFoundError: 'claude'` while the
    identical command in the foreground succeeded — the launch context's
    environment did not carry the directory where `claude` lives.
    Resolving to an absolute path once, here, removes the PATH dependency
    entirely: create_subprocess_exec performs no lookup when given an
    absolute path, so the result is identical foreground, background,
    nested-Claude-Code, or cron.

    Resolution order:
      1. BERIL_CLAUDE_BIN env var (explicit operator override).
      2. shutil.which("claude") against the current PATH.
      3. A fixed list of well-known install locations, including the
         newest ~/.nvm/versions/node/*/bin (Claude Code's npm target).

    Raises RuntimeError listing every location searched if none resolve —
    a loud failure at orchestrator init beats a bare FileNotFoundError
    surfacing several phases into the pipeline.
    """
    # 1. Explicit operator override.
    override = os.environ.get("BERIL_CLAUDE_BIN", "").strip()
    if override:
        p = Path(override).expanduser()
        if p.is_file():
            return str(p.resolve())
        raise RuntimeError(
            f"BERIL_CLAUDE_BIN is set to {override!r} but that path is not "
            "a file. Unset it or point it at the real `claude` binary."
        )

    # 2. PATH lookup.
    found = shutil.which("claude")
    if found:
        return str(Path(found).resolve())

    # 3. Well-known install locations.
    home = Path.home()
    candidates: list[Path] = [
        home / ".local" / "bin" / "claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
        home / ".npm-global" / "bin" / "claude",
    ]
    # nvm installs node (and its global bin) under a versioned dir;
    # search newest-first.
    nvm_root = home / ".nvm" / "versions" / "node"
    if nvm_root.is_dir():
        for node_dir in sorted(nvm_root.iterdir(), reverse=True):
            candidates.append(node_dir / "bin" / "claude")
    for c in candidates:
        if c.is_file():
            return str(c.resolve())

    searched = [
        "$BERIL_CLAUDE_BIN (unset)",
        "shutil.which('claude') -> not found",
    ]
    searched += [str(c) for c in candidates]
    raise RuntimeError(
        "Cannot locate the `claude` CLI — the orchestrator needs it for "
        "every LLM phase. Fix one of:\n"
        "  - set BERIL_CLAUDE_BIN to the absolute path of `claude`, or\n"
        "  - put `claude` on PATH (verify with `which claude`), or\n"
        "  - install Claude Code (https://docs.claude.com).\n"
        "Searched:\n  " + "\n  ".join(searched)
    )


def resolve_adversarial_bin() -> Optional[str]:
    """Resolve the `beril-adversarial` CLI to an absolute path
    (Stage 3 Tier K, 2026-05-16).

    Mirrors resolve_claude_bin's approach but is OPTIONAL: returns the
    absolute path if found, or None if not. The orchestrator treats a
    missing `beril-adversarial` as a degraded-review condition (loud
    warning + fallback inline reviewer), not a hard halt — unlike
    `claude`, which is required by every LLM phase.

    Background: phase_review's Tier-3 call used a bare-name spawn
    (`cmd = ["beril-adversarial", ...]`), the same class of bug Tier J
    fixed for `claude`. Observed on draft_1 of ibd_phage_targeting:
    `beril-adversarial` was on PATH per `configure`, but `phase_review`
    fell through to the inline fallback anyway (silently advancing to
    phase_optimize without a structured findings JSON). Resolving to
    an absolute path here removes the PATH-visibility variable; making
    the fallback path noisy makes the lighter-review state impossible
    to miss.

    Resolution order:
      1. BERIL_ADVERSARIAL_BIN env var (explicit operator override).
      2. shutil.which("beril-adversarial") against the current PATH.
      3. Well-known install locations.

    Returns the absolute path (str) or None. If BERIL_ADVERSARIAL_BIN
    is set but points at a non-file, raises RuntimeError (explicit
    misconfiguration should fail loud, not silently fall through to
    the fallback).
    """
    # 1. Explicit operator override.
    override = os.environ.get("BERIL_ADVERSARIAL_BIN", "").strip()
    if override:
        p = Path(override).expanduser()
        if p.is_file():
            return str(p.resolve())
        raise RuntimeError(
            f"BERIL_ADVERSARIAL_BIN is set to {override!r} but that path "
            "is not a file. Unset it or point it at the real "
            "`beril-adversarial` binary."
        )

    # 2. PATH lookup.
    found = shutil.which("beril-adversarial")
    if found:
        return str(Path(found).resolve())

    # 3. Well-known install locations.
    home = Path.home()
    candidates: list[Path] = [
        home / ".local" / "bin" / "beril-adversarial",
        Path("/opt/homebrew/bin/beril-adversarial"),
        Path("/usr/local/bin/beril-adversarial"),
    ]
    for c in candidates:
        if c.is_file():
            return str(c.resolve())

    # Not found — return None. Caller decides whether to fall back.
    return None


class PaperWriterOrchestrator:
    """Manages the full lifecycle of a paper draft."""

    def _isolate_env(self):
        """Create an environment that strips Claude Code variables to prevent nested sandbox issues."""
        env = os.environ.copy()
        env["CLAUDECODE"] = ""
        return env

    async def _run_claude_p_with_cost(
        self,
        *,
        phase_label: str,
        system_prompt_text: str,
        user_prompt: str,
        model: str | None = None,
        allowed_tools: str | None = None,
    ) -> tuple[int, str, str, float]:
        """Stage 1 Tier D — single helper for claude -p invocations.

        Adds ``--output-format json`` to every call so the envelope's
        ``total_cost_usd`` is captured. Parses it; increments
        ``state.cost_so_far_usd``; persists state.json. Returns
        (returncode, stdout, stderr, cost_usd) so callers can do
        their own post-checks.

        Stage 2 Tier G (2026-05-11): user_prompt is passed via STDIN,
        not as a positional argv argument. The previous version passed
        it positionally and worked for invocations without
        --allowedTools, but failed for invocations WITH --allowedTools
        (the citation_pool failure on draft_5: claude -p exited
        "Input must be provided either through stdin or as a prompt
        argument when using --print"). Passing via stdin sidesteps
        whatever argv-parsing quirk was eating the prompt.
        """
        import json
        cmd = [
            self.claude_bin, "-p",
            "--system-prompt", system_prompt_text,
            "--output-format", "json",
            "--dangerously-skip-permissions",
        ]
        if model:
            cmd.extend(["--model", model])
        if allowed_tools:
            cmd.extend(["--allowedTools", allowed_tools])
        # user_prompt goes via stdin (see Tier G docstring above).

        env = self._isolate_env()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self.draft_dir),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate(
            input=user_prompt.encode("utf-8")
        )
        stdout = stdout_b.decode("utf-8", errors="ignore")
        stderr = stderr_b.decode("utf-8", errors="ignore")

        # Parse cost from envelope. Be defensive: if the envelope isn't
        # parseable or doesn't include total_cost_usd, record 0.0 and
        # continue rather than fail the phase on a telemetry issue.
        cost_usd = 0.0
        envelope_note = ""
        if stdout.strip():
            try:
                envelope = json.loads(stdout.strip())
                if isinstance(envelope, dict):
                    raw = envelope.get("total_cost_usd")
                    if isinstance(raw, (int, float)):
                        cost_usd = float(raw)
                    else:
                        envelope_note = "envelope missing total_cost_usd"
                else:
                    envelope_note = f"envelope not a dict ({type(envelope).__name__})"
            except json.JSONDecodeError as e:
                envelope_note = (
                    f"envelope not parseable JSON: {e.msg}; first 200 chars: "
                    f"{stdout[:200]!r}"
                )

        # Increment + persist.
        if cost_usd > 0.0:
            self.state.cost_so_far_usd = (
                (self.state.cost_so_far_usd or 0.0) + cost_usd
            )
            save_state(self.draft_dir, self.state)
            logger.info(
                f"{phase_label}: +${cost_usd:.4f}; "
                f"cumulative ${self.state.cost_so_far_usd:.4f}"
            )
        elif envelope_note:
            logger.warning(
                f"{phase_label}: cost not captured ({envelope_note}); "
                f"cumulative ${self.state.cost_so_far_usd or 0.0:.4f}"
            )

        # Circuit breaker is invoked here AND on advance_phase so a single
        # over-budget call is caught immediately, not just at phase boundary.
        self._check_circuit_breaker()

        return proc.returncode, stdout, stderr, cost_usd

    def __init__(
        self,
        draft_dir: Path,
        max_cost_usd: Optional[float] = None,
        # Stage 3 (2026-05-12): `model` defaults to Opus, not Sonnet.
        # `model` drives the reasoning-heavy phases — plan (throughline
        # generation), triage (claim extraction + discrepancy audit),
        # the optimizer — which are the most load-bearing decisions in
        # the pipeline and where draft_9's source_notebook regression
        # occurred. The holistic draft was already Opus; the scaffolding
        # phases should not silently fall to Sonnet. The Tier-2 light
        # review stays on Haiku (config.haiku_model) by design.
        model: str = "claude-opus-4-6",
        model_writing: str = "claude-opus-4-6",
        no_adversarial: bool = False,
    ):
        self.draft_dir = draft_dir
        self.project_dir = draft_dir.parent.parent
        self.max_cost_usd = max_cost_usd
        self.model = model
        self.model_writing = model_writing
        # Stage 3 Tier J: resolve `claude` to an absolute path up front,
        # before any state I/O, so a missing CLI fails loud at init
        # rather than as a bare FileNotFoundError mid-pipeline. Also
        # makes the spawn immune to the launch context's PATH.
        self.claude_bin = resolve_claude_bin()
        logger.info(f"Resolved claude CLI: {self.claude_bin}")
        # Stage 3 Tier K (2026-05-16): resolve `beril-adversarial`
        # similarly. Unlike claude this is optional — the orchestrator
        # falls back to the inline `fallback_reviewer.v1.md` prompt
        # when the canonical reviewer is unavailable. But that fallback
        # is a degraded review (3 finding classes vs 10, no literature
        # scan, no biological-claim verification), so we want the user
        # to KNOW when it's about to fire.
        #
        # `no_adversarial=True` is an explicit opt-out (passed by the
        # `--no-adversarial` CLI flag); in that case we don't even
        # attempt resolution. Otherwise we resolve, store, and warn at
        # init if the canonical reviewer is missing so the warning
        # surfaces minutes before phase_review fires.
        self.no_adversarial = no_adversarial
        if no_adversarial:
            self.adversarial_bin: Optional[str] = None
            logger.info(
                "--no-adversarial flag set; Tier-3 review will use the "
                "inline fallback reviewer (lighter scope by design)."
            )
        else:
            self.adversarial_bin = resolve_adversarial_bin()
            if self.adversarial_bin:
                logger.info(
                    f"Resolved beril-adversarial CLI: {self.adversarial_bin}"
                )
            else:
                logger.warning(
                    "beril-adversarial CLI not found on PATH or in any "
                    "well-known install location. Tier-3 review will "
                    "FALL BACK to the lighter inline reviewer "
                    "(fallback_reviewer.v1) — manuscript review will "
                    "MISS biological-accuracy errors, citation-reality "
                    "checks, and drift-from-REPORT analysis that the "
                    "canonical reviewer catches. Install with: "
                    "pipx install beril-adversarial-skill, or set "
                    "BERIL_ADVERSARIAL_BIN to its absolute path."
                )
        self.state: DraftState = self._initialize_state()

    def _initialize_state(self) -> DraftState:
        """Load state from disk or create a fresh one."""
        if not self.draft_dir.exists():
            self.draft_dir.mkdir(parents=True)
            state = DraftState(phase="init", project_id=self.project_dir.name)
            save_state(self.draft_dir, state)
            return state
        return load_state(self.draft_dir)

    def _check_circuit_breaker(self):
        """Enforce the max_cost_usd ceiling."""
        if self.max_cost_usd and self.state.cost_so_far_usd >= self.max_cost_usd:
            logger.error(f"Cost limit exceeded: ${self.state.cost_so_far_usd:.2f} >= ${self.max_cost_usd:.2f}")
            raise TokenLimitExceeded(f"Cumulative spend reached ${self.state.cost_so_far_usd:.2f}")

    def advance_phase(self, new_phase: str):
        """Update phase, check cost limits, and save to disk."""
        if new_phase not in VALID_PHASES:
            raise ValueError(f"Invalid phase: {new_phase}")
        logger.info(f"Advancing phase: {self.state.phase} -> {new_phase}")
        self.state.phase = new_phase
        self.state.touch()
        save_state(self.draft_dir, self.state)
        self._check_circuit_breaker()

    async def run_pipeline(self):
        """Main entry point to execute the pipeline from the current phase."""
        logger.info(f"Starting pipeline run from phase: {self.state.phase}")

        try:
            if self.state.phase == "init":
                self.advance_phase("extract")
            if self.state.phase == "extract":
                await self.phase_extract()
            if self.state.phase == "triage":
                await self.phase_triage()
            if self.state.phase == "plan":
                await self.phase_plan()
            if self.state.phase == "throughline_pick":
                logger.info("Paused at throughline_pick for user handoff.")
                raise PipelineHalted("Waiting for user to select a throughline.")
            if self.state.phase == "citation_pool":
                await self.phase_citation_pool()
            if self.state.phase == "drafting":
                await self.phase_drafting_concurrent()
            # Stage 2 Tier D (2026-05-11): reordered.
            # OLD: drafting → supplementary_pool → review → optimize → compliance_gate
            # NEW: drafting → review → optimize → supplementary_pool → compliance_gate
            # Rationale: the Tier A subtraction-only optimizer inserts
            # [NEEDS CITATION] markers when the adversarial reviewer
            # flags citation_reality. Those markers need supplementary_pool
            # to resolve them via WebSearch. In the old order
            # supplementary_pool ran BEFORE the optimizer ever produced
            # markers, so it always reported "No [NEEDS CITATION] markers
            # found. Skipping." (observed in draft_4).
            if self.state.phase == "review":
                await self.phase_review()
            if self.state.phase == "optimize":
                await self.phase_optimize()
            if self.state.phase == "supplementary_pool":
                await self.phase_supplementary_pool()
            # Stage 1 Tier B: previously this branch raised PipelineHalted
            # unconditionally, so phase_compliance_gate (which does the
            # actual ICMJE checks) never ran. Now we run the gate, then
            # advance to assemble. The autofix sub-phase still raises if
            # user clarification is required.
            if self.state.phase == "compliance_gate":
                await self.phase_compliance_gate()
            if self.state.phase == "assemble":
                await self.phase_assemble()
            # Stage 1 Tier B: removed dead branches.
            #   - "compliance" was not in VALID_PHASES.
            #   - "rewrite" branch called nonexistent phase_rewrite method.
            # If a rewrite cycle is needed, it should go through Phase 2
            # (targeted holistic re-pass) or Phase 4 (selective optimizer).
            if self.state.phase == "assembled":
                logger.info("Pipeline complete. Paper assembled.")
        except PipelineHalted as e:
            logger.info(f"Pipeline paused: {e}")
        except DiscrepancyInteractiveHalt as e:
            logger.warning(f"Interactive Discrepancy Halt: {e}")
            # Emits handoff for the UI
        except Exception as e:
            logger.exception("Pipeline failed unexpectedly.")
            raise

    async def phase_extract(self):
        logger.info("Running extract phase (extract_methods, extract_figures, extract_tables)...")
        
        # Tools path relative to this file
        tools_dir = Path(__file__).parent / "skill" / "tools"
        audit_dir = self.draft_dir / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. extract_methods.py
        if not (self.draft_dir / "methods_provenance.md").exists():
            logger.info("Running extract_methods.py")
            cmd = [sys.executable, str(tools_dir / "extract_methods.py"), str(self.project_dir), "--output-dir", str(self.draft_dir)]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            with open(audit_dir / "extract_methods.log", "wb") as logf:
                logf.write(stdout)
                logf.write(stderr)
            if proc.returncode != 0:
                if proc.returncode == 1:
                    logger.error(f"extract_methods.py: no notebooks found at {self.project_dir}/notebooks/")
                    raise RuntimeError("Halting per LAYOUT 'Extract-tool invocation' contract (exit 1 = halt).")
                logger.warning(f"extract_methods.py exited {proc.returncode}; some notebooks may have failed parse. Continuing.")

        # 2. extract_figures.py
        if not (self.draft_dir / "figures_inventory.md").exists():
            logger.info("Running extract_figures.py")
            cmd = [sys.executable, str(tools_dir / "extract_figures.py"), str(self.project_dir), "--output-dir", str(self.draft_dir)]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            with open(audit_dir / "extract_figures.log", "wb") as logf:
                logf.write(stdout)
                logf.write(stderr)
            if proc.returncode != 0:
                logger.warning(f"extract_figures.py exited {proc.returncode}")

        # 3. extract_tables.py
        if not (self.draft_dir / "tables_inventory.md").exists():
            logger.info("Running extract_tables.py")
            cmd = [sys.executable, str(tools_dir / "extract_tables.py"), str(self.project_dir), "--output-dir", str(self.draft_dir)]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            with open(audit_dir / "extract_tables.log", "wb") as logf:
                logf.write(stdout)
                logf.write(stderr)
            if proc.returncode != 0:
                logger.warning(f"extract_tables.py exited {proc.returncode}")

        self.advance_phase("triage")

    async def phase_triage(self):
        logger.info("Running triage phase... Extracting claims and auditing discrepancies.")
        
        # 1. Extract Claims
        claims_prompt_path = Path(__file__).parent / "skill" / "prompts" / "extract_claims.v1.md"
        report_path = self.project_dir / "REPORT.md"
        methods_path = self.draft_dir / "methods_provenance.md"
        claims_out = self.draft_dir / "claim_inventory.tsv"
        
        if report_path.exists() and not claims_out.exists():
            logger.info("Extracting claims via LLM...")
            user_prompt = f"""
Please execute the extract claims task.
- REPORT_PATH: {report_path}
- METHODS_PATH: {methods_path}
- OUTPUT_PATH: {claims_out}

Write the resulting TSV strictly to OUTPUT_PATH using the Write tool.
"""
            # Stage 3 Tier G (2026-05-12): route through
            # _run_claude_p_with_cost. Previously this was a raw
            # `claude -p` with NO --model flag and NO cost tracking —
            # the only major LLM call in the pipeline that bypassed the
            # Stage 1 Tier D helper. Two consequences observed on
            # draft_9: (1) an unpinned model resolves differently in a
            # nested Claude Code session (BERIL slash-command) than from
            # a plain shell (CLI) — draft_9's claim-extraction model
            # emitted bare-stem / em-dash source_notebook values,
            # blowing the validator clear-rate from ~10% to 76%;
            # (2) the triage LLM spend was missing from
            # state.cost_so_far_usd entirely (draft_9's $7.42 was an
            # undercount). Pinning model=self.model fixes both.
            rc, stdout, stderr, _cost = await self._run_claude_p_with_cost(
                phase_label="phase_triage.extract_claims",
                system_prompt_text=claims_prompt_path.read_text(encoding="utf-8"),
                user_prompt=user_prompt,
                model=self.model,
                allowed_tools="Read,Write,Edit,Bash,Grep,Glob",
            )
            if rc != 0:
                logger.warning(f"Claim extraction failed:\nSTDOUT: {stdout}\nSTDERR: {stderr}")
            else:
                logger.info(f"Wrote {claims_out}")

        # Stage 1 Tier C: post-validate claim_inventory.tsv. The LLM
        # is known to fabricate source_notebook paths (10% on draft_3).
        # The validator marks orphan paths with notes='unresolved-notebook'
        # so downstream Tier 1 / Tier 3 can detect them deterministically.
        if claims_out.exists():
            logger.info("Validating claim_inventory.tsv source_notebooks...")
            validate_tool = Path(__file__).parent / "skill" / "tools" / "validate_claim_inventory.py"
            audit_json = self.draft_dir / "audit" / "claim_inventory_validation.json"
            v_cmd = [
                sys.executable,
                str(validate_tool),
                "--tsv", str(claims_out),
                "--project-root", str(self.project_dir),
                "--audit", str(audit_json),
            ]
            v_proc = await asyncio.create_subprocess_exec(
                *v_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            v_stdout, v_stderr = await v_proc.communicate()
            # Echo validator's summary to our log; non-fatal if it fails
            # (validator is advisory at this stage, not a halt gate).
            if v_proc.returncode != 0:
                logger.warning(
                    f"validate_claim_inventory.py exited {v_proc.returncode}; "
                    f"stderr: {v_stderr.decode('utf-8', errors='ignore')[:500]}"
                )
            else:
                # The validator emits its summary to stderr.
                summary = v_stderr.decode("utf-8", errors="ignore").strip()
                if summary:
                    logger.info(f"claim_inventory validator: {summary}")

        # 2. Audit Discrepancies
        disc_prompt_path = Path(__file__).parent / "skill" / "prompts" / "audit_discrepancies.v1.md"
        plan_path = self.project_dir / "RESEARCH_PLAN.md"
        disc_out = self.draft_dir / "discrepancy_register.md"
        
        if plan_path.exists() and not disc_out.exists():
            logger.info("Auditing discrepancies via LLM...")
            user_prompt = f"""
Please execute the audit discrepancies task.
- RESEARCH_PLAN_PATH: {plan_path}
- METHODS_PATH: {methods_path}
- OUTPUT_PATH: {disc_out}

Write the resulting markdown strictly to OUTPUT_PATH using the Write tool.
"""
            # Stage 3 Tier G (2026-05-12): route through
            # _run_claude_p_with_cost — same rationale as the
            # claim-extraction call above (pin model, capture cost).
            rc, stdout, stderr, _cost = await self._run_claude_p_with_cost(
                phase_label="phase_triage.audit_discrepancies",
                system_prompt_text=disc_prompt_path.read_text(encoding="utf-8"),
                user_prompt=user_prompt,
                model=self.model,
                allowed_tools="Read,Write,Edit,Bash,Grep,Glob",
            )
            if rc != 0:
                logger.warning(f"Discrepancy audit failed:\nSTDOUT: {stdout}\nSTDERR: {stderr}")
            else:
                logger.info(f"Wrote {disc_out}")

        self.advance_phase("plan")

    def _classify_tier_from_candidates(self) -> None:
        """Stage 3 Tier D (2026-05-12): populate state.tier from the plan
        phase's throughline_candidates.md.

        Delegates to `paper_writer_helpers._extract_tier_from_text`,
        which is the canonical regex the bash flow uses (matches
        `**Tier:** {STRONG|THIN|EXPLORATORY}` and the plan.v1 closing-
        message `tier: ...` form). The Python orchestrator was
        previously missing this step, leaving state.tier=None — causing
        downstream consumers (adversarial reviewer; section word-budget
        prompts) to silently default to EXPLORATORY regardless of
        actual project rigor.

        Honors explicit user-set tier (e.g., via state.json edit) so
        manual overrides survive re-runs. Defaults to EXPLORATORY
        (conservative) when the candidates file is missing or the
        regex finds no verdict.
        """
        if self.state.tier in ("STRONG", "THIN", "EXPLORATORY"):
            logger.info(
                f"_classify_tier_from_candidates: tier already set to "
                f"{self.state.tier}; preserving."
            )
            return

        candidates_path = self.draft_dir / "throughline_candidates.md"
        if not candidates_path.is_file():
            self.state.tier = "EXPLORATORY"
            save_state(self.draft_dir, self.state)
            logger.warning(
                f"_classify_tier_from_candidates: {candidates_path} missing; "
                "defaulting tier=EXPLORATORY."
            )
            return

        # Import the canonical extractor — same regex as
        # `paper_writer_helpers.py extract-tier` so Python and bash
        # flows agree.
        sys.path.insert(
            0,
            str(Path(__file__).parent / "skill" / "tools"),
        )
        try:
            from paper_writer_helpers import _extract_tier_from_text  # type: ignore[import-not-found]
        finally:
            # Remove the path we added to keep import space tidy.
            tools_path = str(Path(__file__).parent / "skill" / "tools")
            if sys.path and sys.path[0] == tools_path:
                sys.path.pop(0)

        text = candidates_path.read_text(encoding="utf-8")
        tier = _extract_tier_from_text(text)

        if tier is None:
            logger.warning(
                "_classify_tier_from_candidates: no tier verdict in "
                f"{candidates_path}; defaulting tier=EXPLORATORY."
            )
            tier = "EXPLORATORY"
        else:
            logger.info(
                f"_classify_tier_from_candidates: tier={tier} extracted "
                f"from {candidates_path.name}."
            )

        self.state.tier = tier
        save_state(self.draft_dir, self.state)

    async def phase_plan(self):
        logger.info("Running plan phase... Generating throughline candidates.")
        prompt_path = Path(__file__).parent / "skill" / "prompts" / "plan.v1.md"
        if not prompt_path.exists():
            logger.error(f"Missing prompt: {prompt_path}")
            raise RuntimeError("Missing plan.v1.md prompt")
            
        user_prompt = f"""
Please run the plan phase.
- PROJECT_ROOT: {self.project_dir}
- DRAFT_DIR: {self.draft_dir}
- THROUGHLINE_CANDIDATES_PATH: {self.draft_dir / 'throughline_candidates.md'}
- REPORT_PATH: {self.project_dir / 'REPORT.md'}
- RESEARCH_PLAN_PATH: {self.project_dir / 'RESEARCH_PLAN.md'}
- NOTEBOOKS_DIR: {self.project_dir / 'notebooks'}
- ANALYSIS_REQUESTS_PATH: {self.project_dir / 'analysis_requests.md'}

Also make sure to create the .handoff.json file!
"""
        # Stage 1 Tier D: cost-tracking helper.
        rc, stdout, stderr, cost = await self._run_claude_p_with_cost(
            phase_label="phase_plan",
            system_prompt_text=prompt_path.read_text(encoding='utf-8'),
            user_prompt=user_prompt,
            model=self.model,
        )
        if rc != 0:
            logger.error(
                f"Claude CLI failed for plan phase:\nSTDOUT: {stdout}\n"
                f"STDERR: {stderr}"
            )
            raise RuntimeError("Plan phase failed")

        # Stage 3 Tier D (2026-05-12): tier verdict from plan's
        # throughline_candidates.md. Bash flow already runs
        # paper_writer_helpers.py extract-tier here; the Python
        # orchestrator was missing the equivalent call, leaving
        # state.tier=None for the rest of the pipeline.
        self._classify_tier_from_candidates()

        self.advance_phase("throughline_pick")

    async def phase_citation_pool(self):
        logger.info("Running citation pool construction...")
        prompt_path = Path(__file__).parent / "skill" / "prompts" / "citation_pool.v1.md"
        if not prompt_path.exists():
            logger.warning(f"Prompt {prompt_path} not found, skipping citation pool.")
            self.advance_phase("drafting")
            return

        citation_pool_path = self.draft_dir / "citation_pool.json"

        # Stage 2 Tier C: idempotency. If the pool already exists (from
        # a prior run), don't burn LLM cost rebuilding it. The pool is
        # expensive ($1-3 per SPEC §4.2) and stable across runs once
        # built — the inputs (RESEARCH_PLAN + REPORT) rarely change.
        if citation_pool_path.is_file() and citation_pool_path.stat().st_size > 0:
            logger.info(
                f"citation_pool.json already exists at {citation_pool_path} "
                f"({citation_pool_path.stat().st_size} bytes); skipping rebuild."
            )
            self.advance_phase("drafting")
            return

        user_prompt = f"""
Please build the verified citation pool per the system prompt.
- PROJECT_ROOT: {self.project_dir}
- DRAFT_DIR: {self.draft_dir}
- CITATION_POOL_PATH: {citation_pool_path}
- RESEARCH_PLAN_PATH: {self.project_dir / 'RESEARCH_PLAN.md'}
- REPORT_PATH: {self.project_dir / 'REPORT.md'}

Write the pool JSON to CITATION_POOL_PATH using the Write tool.
"""
        # Stage 2 Tier C: grant filesystem + web search tools. Previously
        # this had none, so the LLM couldn't actually create the file the
        # prompt told it to create. citation_pool.v1.md needs Read (for
        # research_plan + report), WebSearch (for literature lookup),
        # and Write (for the JSON output).
        rc, stdout, stderr, cost = await self._run_claude_p_with_cost(
            phase_label="phase_citation_pool",
            system_prompt_text=prompt_path.read_text(encoding='utf-8'),
            user_prompt=user_prompt,
            model=self.model,
            allowed_tools="Read,Write,Edit,Bash,Grep,Glob,WebSearch,WebFetch",
        )
        # Stage 2 Tier I (2026-05-11): success detection.
        # The citation_pool LLM is a 47-turn agent and Anthropic's
        # content-moderation filter sometimes blocks the closing
        # message AFTER Write tool calls have succeeded (observed on
        # draft_6: $1.26 spent; 24 citations on disk; subprocess
        # exited non-zero with "Output blocked by content filtering
        # policy"). The actual deliverable — citation_pool.json with
        # populated citations[] — is what matters. Parse the file
        # and decide success/failure on its content, not on the
        # subprocess exit code.
        pool_status = self._evaluate_citation_pool(
            citation_pool_path, rc, stdout, stderr,
        )
        if pool_status == "ok":
            logger.info(
                f"citation_pool.json written ({citation_pool_path.stat().st_size} bytes; "
                f"pool successfully built)."
            )
        elif pool_status == "filter_blocked_but_recovered":
            logger.warning(
                f"citation_pool subprocess exited non-zero (likely content-filter "
                f"blocked closing message) but citation_pool.json has valid "
                f"entries on disk. Proceeding with the partial pool."
            )
        elif pool_status == "empty":
            logger.warning(
                f"citation_pool.json exists at {citation_pool_path} but has no "
                f"citations[] entries. Holistic draft will fall back to "
                f"[NEEDS CITATION] markers."
            )
        else:  # missing
            logger.warning(
                f"citation_pool.json was NOT created at {citation_pool_path}. "
                f"rc={rc}. STDERR head: {stderr[:300]!r}. "
                f"Holistic draft will fall back to [NEEDS CITATION] markers."
            )

        # Stage 2 Tier M (2026-05-11): normalize the pool's `key` field.
        # The citation_pool prompt's schema marks `bib_key` as optional;
        # in practice the LLM often omits it (observed on draft_7: 46
        # entries, 0 with a `key` field). The downstream consumers
        # (holistic_draft, supplementary_pool) cite by [key] form,
        # which means the chain breaks without a key. Normalize here:
        # for every entry that lacks `key`, derive
        # `<FirstAuthorLastName><Year>` and write back. Deterministic
        # post-processing; LLM-schema-fragility-proof.
        if pool_status in ("ok", "filter_blocked_but_recovered"):
            self._normalize_citation_pool_keys(citation_pool_path)

        self.advance_phase("drafting")

    def _normalize_citation_pool_keys(self, pool_path: Path) -> None:
        """Stage 2 Tier M (2026-05-11). Ensure every pool entry has a
        `key` field. Derive from authors+year when missing or empty.

        Key form: ``<FirstAuthorLastName><Year>``. Examples:
          ``"authors": "Lloyd-Price J, et al.", "year": 2019``
            → key = "Lloyd-Price2019"
          ``"authors": "Devlin AS, Fischbach MA", "year": 2015``
            → key = "Devlin2015"
          ``"authors": "Smith, J., Doe, K.", "year": 2024``
            → key = "Smith2024"

        Disambiguation: if two entries derive the same key, append
        ``a``, ``b``, ``c`` lowercase letter (Year-suffix convention).

        Side effects:
          - Rewrites pool_path with normalized entries (citations[] in
            their original order, all with non-empty `key`).
          - Logs counts (entries-normalized, total-pool-size).
          - Idempotent: re-running on a normalized pool is a no-op
            (every entry already has `key`).
        """
        import json
        import re
        if not pool_path.is_file():
            return
        try:
            data = json.loads(pool_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                f"_normalize_citation_pool_keys: cannot parse {pool_path}: {e}. "
                "Skipping normalization."
            )
            return
        if not isinstance(data, dict):
            return
        # Accept both schema-key spellings (`citations` from the
        # citation_pool.v1 prompt; `entries` from earlier SPEC drafts).
        array_key = (
            "citations" if isinstance(data.get("citations"), list)
            else "entries" if isinstance(data.get("entries"), list)
            else None
        )
        if array_key is None:
            return
        cites = data[array_key]
        normalized = 0
        used_keys: set[str] = set()

        def _derive_key(authors_str: str, year_val) -> str:
            """First-author last name + year. Handles common author-string
            shapes observed in real pools:

              "Dahlhamer JM, et al."     → "Dahlhamer"
              "Lloyd-Price J, et al."    → "Lloyd-Price"  (hyphenated last name)
              "Vich-Vila A, et al."      → "Vich-Vila"
              "Devlin AS, Fischbach MA"  → "Devlin"
              "Smith, J., Doe, K."       → "Smith"
              "Someone S"                → "Someone"
              "Single"                   → "Single"
            """
            first = authors_str.strip()
            # Step 1: cut off everything after first author boundary.
            # Boundaries (whichever comes first):
            #   "<space>et al" — e.g., "Lloyd-Price J, et al."
            #   ", <Capital>"  — e.g., "Devlin AS, Fischbach MA" or "Smith, John"
            #   ";"            — author-list separator
            first = re.split(
                r"\s+et\s+al|,\s*[A-Z]|;", first, maxsplit=1,
            )[0].strip()
            # At this point `first` is either "LastName" or
            # "LastName F" or "LastName FM" (last name + initials,
            # space-separated). Take the chunk before the first space:
            # that's the last name (preserving hyphens).
            if " " in first:
                first = first.split()[0]
            # Defensive cleanup: keep letters + hyphens; drop anything else.
            first = re.sub(r"[^A-Za-z\-]", "", first) or "Unknown"
            year_str = str(year_val) if year_val else "Unknown"
            return f"{first}{year_str}"

        def _disambiguate(base: str, used: set[str]) -> str:
            """Append a/b/c... until the key is unique."""
            if base not in used:
                return base
            suffix = "a"
            while f"{base}{suffix}" in used:
                suffix = chr(ord(suffix) + 1)
            return f"{base}{suffix}"

        for entry in cites:
            if not isinstance(entry, dict):
                continue
            existing = (entry.get("key") or entry.get("bib_key") or "").strip()
            if existing:
                # Already has a key: keep it; collision unlikely with
                # explicit keys but disambiguate defensively.
                final_key = _disambiguate(existing, used_keys)
                entry["key"] = final_key
                used_keys.add(final_key)
                continue
            # Derive from authors+year.
            base = _derive_key(
                str(entry.get("authors", "")), entry.get("year"),
            )
            key = _disambiguate(base, used_keys)
            entry["key"] = key
            used_keys.add(key)
            normalized += 1

        pool_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if normalized > 0:
            logger.info(
                f"citation_pool key normalization: {normalized}/{len(cites)} "
                f"entries given derived keys; pool now has {len(used_keys)} unique keys."
            )
        else:
            logger.info(
                f"citation_pool key normalization: all {len(cites)} entries "
                f"already had keys; no changes."
            )

    def _evaluate_citation_pool(
        self, pool_path: Path, rc: int, stdout: str, stderr: str,
    ) -> str:
        """Stage 2 Tier I helper. Decide pool status from disk artifact,
        not subprocess exit code.

        Returns one of:
          "ok"                          — file exists, valid JSON, citations[] non-empty
          "filter_blocked_but_recovered"— same as ok but subprocess rc != 0
          "empty"                       — file exists, valid JSON, citations[] empty
          "missing"                     — file missing or unreadable

        We accept either `citations` or `entries` as the array key (the
        prompt's schema vs. the SPEC schema; both shapes have been
        observed in the wild).
        """
        import json
        if not pool_path.is_file():
            return "missing"
        try:
            text = pool_path.read_text(encoding="utf-8")
            if not text.strip():
                return "missing"
            data = json.loads(text)
        except (json.JSONDecodeError, OSError):
            return "missing"
        if not isinstance(data, dict):
            return "empty"
        # Accept both schema key names.
        cites = data.get("citations")
        if cites is None:
            cites = data.get("entries")
        if not isinstance(cites, list) or len(cites) == 0:
            return "empty"
        # Has entries: success.
        if rc == 0:
            return "ok"
        return "filter_blocked_but_recovered"

    async def phase_drafting_concurrent(self):
        # Renamed internally to holistic, but keeping the state name "drafting"
        logger.info("Running holistic drafting phase (M2)...")
        start = time.time()

        prompt_path = Path(__file__).parent / "skill" / "prompts" / "holistic_draft.v1.md"
        if not prompt_path.exists():
            logger.error(f"Prompt {prompt_path} not found.")
            raise RuntimeError("Missing holistic draft prompt.")

        user_prompt = f"""
Please draft the entire manuscript.
- PROJECT_ROOT: {self.project_dir}
- DRAFT_DIR: {self.draft_dir}
- ASSEMBLED_PATH: {self.draft_dir / 'manuscript.md'}
- METHODS_PROVENANCE_PATH: {self.project_dir / 'methods_provenance.md'}
- RESEARCH_PLAN_PATH: {self.project_dir / 'RESEARCH_PLAN.md'}
- REPORT_PATH: {self.project_dir / 'REPORT.md'}
- CITATION_POOL_PATH: {self.draft_dir / 'citation_pool.json'}
- THROUGHLINE_PATH: {self.draft_dir / '00_throughline.md'}
- CLAIM_INVENTORY_PATH: {self.draft_dir / 'claim_inventory.tsv'}
- FIGURES_INVENTORY_PATH: {self.draft_dir / 'figures_inventory.md'}
- TABLES_INVENTORY_PATH: {self.draft_dir / 'tables_inventory.md'}
- REFRAMING_LOG_PATH: {self.draft_dir / 'reframing_log.md'}
- MODE: {self.state.mode}
- TIER: {self.state.tier or 'STRONG'}
"""
        # Stage 1 Tier D: use cost-tracking helper. Holistic draft is
        # the single largest LLM call ($4-8 expected per SPEC §6.7).
        rc, stdout, stderr, cost = await self._run_claude_p_with_cost(
            phase_label="phase_drafting (Opus)",
            system_prompt_text=prompt_path.read_text(encoding='utf-8'),
            user_prompt=user_prompt,
            model=self.model_writing,
        )
        if rc != 0:
            logger.error(
                f"Holistic draft failed:\nSTDOUT: {stdout}\n"
                f"STDERR: {stderr}"
            )
            raise RuntimeError("Holistic drafting failed.")

        logger.info(f"Holistic drafting completed in {time.time() - start:.2f}s")
        
        # Post-draft audits (interactive check)
        await self._audit_discrepancies_interactive()

        # Stage 2 Tier D: drafting → review (was: → supplementary_pool).
        # supplementary_pool now runs after optimize so it can resolve
        # the [NEEDS CITATION] markers the optimizer may insert.
        self.advance_phase("review")


    async def _audit_discrepancies_interactive(self):
        logger.info("Running LLM discrepancy audit...")
        prompt_path = Path(__file__).parent / "skill" / "prompts" / "audit_discrepancies.v1.md"
        if not prompt_path.exists():
            return
            
        user_prompt = f"""
Please generate the discrepancy audit JSON.
- RESEARCH_PLAN_PATH: {self.project_dir / 'RESEARCH_PLAN.md'}
- METHODS_PROVENANCE_PATH: {self.project_dir / 'methods_provenance.md'}
- AUDIT_OUTPUT_PATH: {self.draft_dir / 'audit_discrepancies.json'}

Use the Write tool to write your JSON directly to AUDIT_OUTPUT_PATH.
"""
        cmd = [
            self.claude_bin, "-p",
            "--model", self.model,
            "--system-prompt", prompt_path.read_text(encoding='utf-8'),
            "--dangerously-skip-permissions",
            user_prompt
        ]

        env = self._isolate_env()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self.draft_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error(f"Audit discrepancies failed:\nSTDOUT: {stdout.decode()}\nSTDERR: {stderr.decode('utf-8', errors='ignore')}")
            
        logger.warning("Pausing for asynchronous review of audit_discrepancies.json")




    async def phase_supplementary_pool(self):
        logger.info("Running supplementary citation round (M5)...")
        assembled_path = self.draft_dir / "manuscript.md"
        if assembled_path.exists():
            content = assembled_path.read_text(encoding="utf-8")
            # Stage 2 Tier H (2026-05-11): detect both marker shapes.
            # The original check used "[NEEDS CITATION]" (literal closing
            # bracket immediately after CITATION), but the holistic_draft
            # Tier E discipline emits "[NEEDS CITATION: <topic>]" where
            # the closing bracket comes AFTER the topic. The literal
            # substring "[NEEDS CITATION]" never appears in the latter
            # shape, so all 29 markers in draft_5 were missed. Now we
            # match the OPEN-bracket-plus-prefix substring which covers
            # both shapes.
            marker_prefix = "[NEEDS CITATION"
            marker_count = content.count(marker_prefix)
            if marker_count > 0:
                logger.info(
                    f"{marker_count} [NEEDS CITATION] marker(s) found. "
                    "Invoking supplementary citations."
                )
                prompt_path = Path(__file__).parent / "skill" / "prompts" / "supplementary_citations.v1.md"
                if prompt_path.exists():
                    user_prompt = f"""
Please resolve the [NEEDS CITATION: <topic>] markers in the manuscript.
- ASSEMBLED_PATH: {assembled_path}
- CITATION_POOL_PATH: {self.draft_dir / 'citation_pool.json'}

There are {marker_count} markers to resolve. Each marker has the form
[NEEDS CITATION: <topic>] where <topic> is a 5-10 word description of
what the citation should support. Use WebSearch to find verified
literature, add entries to CITATION_POOL_PATH, and replace each marker
with the citation key in the manuscript.
"""
                    # Stage 2 Tier H: use the cost-tracking helper +
                    # grant tools (previously this call had no
                    # --allowedTools, mirroring the citation_pool bug).
                    rc, stdout, stderr, cost = await self._run_claude_p_with_cost(
                        phase_label="phase_supplementary_pool",
                        system_prompt_text=prompt_path.read_text(encoding='utf-8'),
                        user_prompt=user_prompt,
                        model=self.model,
                        allowed_tools="Read,Write,Edit,Bash,Grep,Glob,WebSearch,WebFetch",
                    )
                    if rc != 0:
                        logger.error(
                            f"Supplementary citation failed:\n"
                            f"STDOUT: {stdout}\nSTDERR: {stderr}"
                        )
            else:
                logger.info("No [NEEDS CITATION] markers found. Skipping.")

        # Stage 4 Tier R-2 (2026-05-17): render references.md /
        # citation_map.md from the final post-supplementary pool. The
        # citation_pool.v1.md prompt explicitly tells the LLM NOT to
        # write those two files because the renderer is the
        # post-processor; the bash flow (paper_writer.sh phase_assemble)
        # invoked `citation_pool.py finalize` for exactly this purpose,
        # but the Python orchestrator port dropped the call, so the
        # files have been 0 bytes since v0.8.0. This call wires the
        # contract back together against the v0.8 holistic flow.
        self._finalize_citation_render()

        # Stage 2 Tier D: supplementary_pool → compliance_gate
        # (was: → review). In the new order, supplementary_pool runs
        # AFTER review/optimize, so the next step is compliance.
        self.advance_phase("compliance_gate")

    def _finalize_citation_render(self) -> None:
        """Stage 4 Tier R-2: render references.md / citation_map.md /
        bibliography.bib from the final post-supplementary citation pool.

        Walks manuscript.md (v0.8 holistic flow) — or per-section files
        when present (legacy sectional flow) — for ``[bib_key]`` marks,
        populates the pool's ``citation_map`` and ``first_cited_at`` in
        first-citation order, then writes the four rendered artifacts to
        the draft directory.

        Failure mode: advisory. A non-existent pool, malformed pool
        JSON, or missing manuscript.md logs a WARNING and returns —
        the pipeline continues toward compliance_gate and assemble.
        The empty files would just regress to today's behavior (the
        bug we are fixing), so degrading silently is acceptable.

        Side effects on disk:
          - <draft_dir>/references.md      (rendered bibliography)
          - <draft_dir>/citation_map.md    (claim/section → ref# index)
          - <draft_dir>/bibliography.bib   (standard BibTeX)
          - <draft_dir>/citation_pool.json (overwritten with citation_map populated)
          - <draft_dir>/finalize_warnings.md (orphan-citation warnings)
        """
        # Late import — citation_pool depends on the dataclasses package
        # and a clean import of skill.tools, both heavier than this
        # orchestrator's critical-path startup cost. Loading here keeps
        # the orchestrator's import graph thin.
        from beril_paper_writer.skill.tools import citation_pool as cp

        pool_path = self.draft_dir / "citation_pool.json"
        if not pool_path.is_file():
            logger.warning(
                f"Stage 4 Tier R-2: pool file {pool_path} not present; "
                "skipping citation render. references.md / citation_map.md "
                "will be empty — this regresses to the pre-fix state."
            )
            return

        try:
            raw = pool_path.read_text(encoding="utf-8")
            import json as _json
            pool = cp.CitationPool.from_dict(_json.loads(raw))
        except (OSError, ValueError) as e:
            logger.warning(
                f"Stage 4 Tier R-2: failed to load pool from {pool_path}: "
                f"{e!r}. Skipping citation render."
            )
            return

        cp.assign_bib_keys(pool)  # idempotent

        try:
            ordered_keys, locations = cp.extract_citekeys_in_first_citation_order(
                self.draft_dir,
            )
        except OSError as e:
            logger.warning(
                f"Stage 4 Tier R-2: failed walking draft for [bib_key] marks: "
                f"{e!r}. Skipping citation render."
            )
            return

        pool_keys = {e.bib_key for e in pool.entries if e.bib_key}
        resolved_keys = [k for k in ordered_keys if k in pool_keys]
        orphan_keys = [k for k in ordered_keys if k not in pool_keys]

        pool.citation_map = {}
        pool.first_cited_at = {}
        cp.assign_citation_numbers(pool, resolved_keys)
        for key, section, para in locations:
            if key not in pool.citation_map:
                continue
            n = pool.citation_map[key]
            if n in pool.first_cited_at:
                continue
            pool.first_cited_at[n] = {"section": section, "paragraph": str(para)}

        try:
            paths = cp.serialize_to_disk(
                pool, self.draft_dir, pool_filename=pool_path.name,
            )
        except (OSError, ValueError) as e:
            logger.warning(
                f"Stage 4 Tier R-2: serialize_to_disk failed: {e!r}. "
                "Some rendered files may be partial."
            )
            return

        # Orphan warnings (mirror _cmd_finalize's behavior, but written
        # only when there's something to say).
        if orphan_keys:
            warnings_path = self.draft_dir / "finalize_warnings.md"
            lines = ["# Citation Finalize Warnings", ""]
            lines.append(
                f"**{len(orphan_keys)} orphaned citation(s)** — bib_keys "
                f"cited in prose but not present in the pool. The user "
                f"must add these to the pool (or remove the citation "
                f"from prose) before submission."
            )
            lines.append("")
            for key in orphan_keys:
                occurrences = [(s, p) for (k, s, p) in locations if k == key]
                lines.append(f"- `[{key}]` — orphaned (not in pool):")
                for s, p in occurrences[:5]:
                    lines.append(f"    - {s}, paragraph {p}")
                if len(occurrences) > 5:
                    lines.append(
                        f"    - ...and {len(occurrences) - 5} more occurrence(s)"
                    )
            warnings_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            logger.warning(
                f"Stage 4 Tier R-2: {len(orphan_keys)} orphan citation(s) "
                f"written to {warnings_path}."
            )

        logger.info(
            f"Stage 4 Tier R-2: citation render complete. "
            f"{len(resolved_keys)} cited, {len(orphan_keys)} orphaned, "
            f"{len(pool.entries) - len(resolved_keys)} pool entries uncited. "
            f"Files rewritten: {sorted(paths.keys())}."
        )

    def _run_tier1_deterministic_checks(self) -> None:
        """Stage 4 Tier T-2 (2026-05-17): the deterministic Tier-1 check
        cascade. Currently exercises a single check (numeric grounding
        via check_numeric_grounding); other deterministic post-checkers
        in the ``check_*`` family can be hung off this helper as they
        gain orchestrator wiring.

        Numeric grounding runs the ``check_numeric_grounding.run_grounding``
        pure function against the assembled ``manuscript.md``, against
        the Phase-0 ``claim_inventory.tsv`` (Tier A) and the project's
        ``REPORT.md`` (Tier B). Output: ``audit/numeric_grounding.json``
        with the standard schema_version="v1" envelope. Strict-mode
        severity: every ungrounded number is P0.

        Failure modes are advisory: a missing manuscript, missing
        inventory, or malformed input logs a WARNING and returns —
        the pipeline continues into Tier 2 (Haiku) and Tier 3
        (canonical adversarial). The Tier-3 reviewer is the
        higher-cost fallback for everything Tier 1 doesn't catch
        (semantic misuse, register drift, register-correct fabrications
        whose number happens to exist elsewhere in REPORT).
        """
        # Late import for the same reasons _finalize_citation_render
        # uses late imports (heavy dataclasses dependency graph at
        # the orchestrator's critical-path startup).
        from beril_paper_writer.skill.tools import (
            check_numeric_grounding as cng,
        )
        from beril_paper_writer.skill.tools.check_numeric_grounding import (
            GroundingReport,
        )
        import json as _json
        from dataclasses import asdict as _asdict

        manuscript_path = self.draft_dir / "manuscript.md"
        if not manuscript_path.is_file():
            logger.warning(
                f"Stage 4 Tier T-2: manuscript.md missing at "
                f"{manuscript_path}; Tier 1 numeric-grounding check "
                "skipped."
            )
            return

        inventory_path = self.draft_dir / "claim_inventory.tsv"
        report_path = self.project_dir / "REPORT.md"

        try:
            manuscript_text = manuscript_path.read_text(encoding="utf-8")
            inventory_claim_texts = cng.load_inventory_claim_texts(
                inventory_path,
            )
            inventory_normalized = cng.build_inventory_normalized_set(
                inventory_claim_texts,
            )
            report_normalized = cng.build_report_normalized_set(
                report_path if report_path.is_file() else None,
            )
            findings, allowlisted, totals = cng.run_grounding(
                manuscript_text,
                inventory_normalized,
                report_normalized,
            )
        except (OSError, ValueError) as exc:
            logger.warning(
                f"Stage 4 Tier T-2: numeric-grounding check failed: "
                f"{exc!r}. Continuing into Tier 2."
            )
            return

        notes: list[str] = []
        if not inventory_path.is_file():
            notes.append(
                "claim_inventory.tsv missing — Tier A grounding "
                "disabled. Run phase_triage to produce it."
            )
        if not report_path.is_file():
            notes.append(
                "REPORT.md not found via project_dir — Tier B "
                "grounding disabled."
            )

        report = GroundingReport(
            schema_version=cng.SCHEMA_VERSION,
            tool="check_numeric_grounding",
            tool_version=cng.TOOL_VERSION,
            draft_dir=str(self.draft_dir),
            manuscript_path=str(manuscript_path),
            inventory_path=str(inventory_path),
            report_path=str(report_path) if report_path.is_file() else None,
            totals=totals,
            findings=[f.to_dict() for f in findings],
            allowlisted=[a.to_dict() for a in allowlisted],
            notes=notes,
        )

        audit_dir = self.draft_dir / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        out_path = audit_dir / "numeric_grounding.json"
        out_path.write_text(
            _json.dumps(_asdict(report), indent=2) + "\n",
            encoding="utf-8",
        )

        # Logging level keys off whether any findings landed.
        # Ungrounded > 0 is the gate signal for Stage 4 Tier S (P0
        # remediation loop, to-be-implemented); for Tier T-2 today we
        # surface as WARNING but advance.
        summary = (
            f"Stage 4 Tier T-2 (numeric grounding): "
            f"{totals['numeric_matches_in_manuscript']} matches; "
            f"{totals['grounded_tier_a_inventory']} grounded(Tier A); "
            f"{totals['grounded_tier_b_report_md']} grounded(Tier B); "
            f"{totals['allowlisted']} allowlisted; "
            f"{totals['ungrounded']} UNGROUNDED → {out_path}"
        )
        if totals["ungrounded"] > 0:
            logger.warning(summary)
            # Surface up to 5 ungrounded findings inline so the
            # operator notices without opening the JSON.
            for f in findings[:5]:
                logger.warning(
                    f"  ungrounded: {f.section} para {f.paragraph} "
                    f"({f.match_class}): {f.matched_text!r}"
                )
        else:
            logger.info(summary)

    async def phase_review(self):
        logger.info("Running tiered review cascade (M3)...")
        # Tier 1: Deterministic
        self._run_tier1_deterministic_checks()

        # Tier 2: Haiku Light
        logger.info(f"Tier 2: Haiku Light review using {config.haiku_model}")
        prompt_path = Path(__file__).parent / "skill" / "prompts" / "haiku_review.v1.md"
        if prompt_path.exists():
            user_prompt = f"Review ASSEMBLED_PATH: {self.draft_dir / 'manuscript.md'}"
            cmd = [
                self.claude_bin, "-p",
                "--model", config.haiku_model,
                "--system-prompt", prompt_path.read_text(encoding='utf-8'),
                "--dangerously-skip-permissions",
                user_prompt
            ]
            env = self._isolate_env()
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(self.draft_dir), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
        # Tier 3: Canonical Adversarial OR loud-warn fallback
        # (Stage 3 Tier K, 2026-05-16).
        #
        # Branch logic:
        #   1. self.no_adversarial → explicit opt-out (--no-adversarial
        #      flag). Run inline fallback; log INFO (user knows).
        #   2. self.adversarial_bin is None → canonical reviewer not
        #      installed. Run inline fallback; log WARNING (user may not
        #      have realized) + write audit/review_mode.json so it's
        #      machine-discoverable.
        #   3. self.adversarial_bin is set → invoke canonical reviewer
        #      via absolute path (Tier J discipline: no bare-name PATH
        #      lookup). On non-zero exit, log ERROR but advance — the
        #      optimizer's missing-findings check will skip cleanly.
        #
        # Stage 4 Tier R-3 (2026-05-17): the defensive `.touch()` of
        # references.md / citation_map.md that lived here has been
        # removed. It was a workaround for the missing
        # citation_pool.finalize call (now wired in
        # phase_supplementary_pool via _finalize_citation_render), and
        # actively masked the contract break — review consumers saw
        # empty files instead of MissingFile and so never flagged the
        # bug until draft_1 of the v0.8.0 live test. Adversarial
        # reviewer tolerates missing files; if it ever doesn't, the
        # fix belongs there, not in a paper-writer-side stub.

        if self.no_adversarial:
            logger.info(
                "Tier 3: --no-adversarial flag set; running inline "
                "fallback reviewer (lighter scope by design)."
            )
            await self._run_fallback_reviewer(reason="explicit-opt-out")
        elif self.adversarial_bin is None:
            logger.warning(
                "Tier 3: beril-adversarial CLI is unavailable — running "
                "the inline FALLBACK reviewer. This is a LIGHTER review "
                "(3 finding classes vs 10; no literature scan; no "
                "biological-claim verification; no drift-from-REPORT "
                "cross-check). The optimizer cannot dispatch on the "
                "fallback's markdown output and will skip. Manuscript "
                "will ship with whatever errors the canonical reviewer "
                "would have caught. Install beril-adversarial-skill "
                "(pipx install ...) or set BERIL_ADVERSARIAL_BIN."
            )
            await self._run_fallback_reviewer(reason="adversarial-missing")
        else:
            logger.info(
                f"Tier 3: Canonical Adversarial review via {self.adversarial_bin}"
            )
            env = self._isolate_env()
            cmd = [
                self.adversarial_bin, "review", "--type", "paper",
                str(self.draft_dir),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.draft_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error(
                    f"Adversarial review failed (rc={proc.returncode}):\n"
                    f"STDOUT: {stdout.decode('utf-8', errors='ignore')[:2000]}\n"
                    f"STDERR: {stderr.decode('utf-8', errors='ignore')[:2000]}"
                )
                # Record the failure mode so downstream / audit knows.
                self._write_review_mode(
                    reviewer="canonical-failed",
                    note=f"beril-adversarial exited {proc.returncode}; see logs",
                )
            else:
                self._write_review_mode(reviewer="canonical")

        self.advance_phase("optimize")

    async def _run_fallback_reviewer(self, *, reason: str) -> None:
        """Stage 3 Tier K: invoke the inline fallback reviewer.

        Used when the canonical `beril-adversarial` CLI is unavailable
        or when `--no-adversarial` is set explicitly. The fallback
        produces a markdown review file (NOT the structured
        adversarial_review.json the optimizer dispatches on), so the
        downstream optimizer will skip cleanly with a missing-findings
        warning. The user reads the markdown manually.

        `reason` is a short tag recorded in audit/review_mode.json so
        the run state is machine-discoverable.
        """
        prompt_path = (
            Path(__file__).parent / "skill" / "prompts"
            / "fallback_reviewer.v1.md"
        )
        if not prompt_path.is_file():
            logger.error(
                f"Fallback reviewer prompt missing at {prompt_path}; "
                "Tier 3 is silently skipped — manuscript ships unreviewed."
            )
            self._write_review_mode(
                reviewer="none",
                note="fallback prompt file missing on disk",
            )
            return

        reviews_dir = self.draft_dir / "reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)
        review_out = reviews_dir / "fallback_review.md"

        user_prompt = (
            f"Run the inline fallback reviewer per the system prompt.\n"
            f"- ASSEMBLED_PATH: {self.draft_dir / 'manuscript.md'}\n"
            f"- REPORT_PATH: {self.project_dir / 'REPORT.md'}\n"
            f"- THROUGHLINE_PATH: {self.draft_dir / '00_throughline.md'}\n"
            f"- CITATION_POOL_PATH: {self.draft_dir / 'citation_pool.json'}\n"
            f"- REVIEW_OUT_PATH: {review_out}\n"
            f"\nWrite REVIEW_OUT_PATH via the Write tool, then emit the "
            f"closing-message template."
        )

        rc, stdout, stderr, _cost = await self._run_claude_p_with_cost(
            phase_label="phase_review.fallback",
            system_prompt_text=prompt_path.read_text(encoding="utf-8"),
            user_prompt=user_prompt,
            model=self.model,
            allowed_tools="Read,Write,Edit,Grep,Glob",
        )
        if rc != 0:
            logger.error(
                f"Fallback reviewer failed (rc={rc}):\n"
                f"STDOUT: {stdout[:1000]}\nSTDERR: {stderr[:1000]}"
            )
            self._write_review_mode(
                reviewer="fallback-failed",
                note=f"fallback reviewer exited {rc}; reason={reason}",
            )
            return

        self._write_review_mode(reviewer="fallback", note=f"reason={reason}")
        logger.warning(
            f"Tier 3 fallback review written to {review_out}. The "
            "optimizer (Phase 4) will skip — no structured findings "
            "JSON. Read the markdown review manually before relying "
            "on this manuscript."
        )

    def _write_review_mode(
        self, *, reviewer: str, note: str = "",
    ) -> None:
        """Stage 3 Tier K: record which Tier-3 reviewer ran, so the
        run state is machine-discoverable in audit/.

        Values for `reviewer`:
          - "canonical"        — beril-adversarial succeeded
          - "canonical-failed" — beril-adversarial exited non-zero
          - "fallback"         — inline fallback_reviewer.v1 used
          - "fallback-failed"  — inline fallback also failed
          - "none"             — Tier-3 skipped entirely
        """
        import json
        audit_dir = self.draft_dir / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        out = audit_dir / "review_mode.json"
        payload = {
            "reviewer": reviewer,
            "note": note,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        out.write_text(
            json.dumps(payload, indent=2), encoding="utf-8",
        )

    async def phase_optimize(self):
        logger.info("Running selective optimizers (M4)...")
        prompt_path = Path(__file__).parent / "skill" / "prompts" / "optimizer.v1.md"
        findings_path = self.draft_dir / "audit" / "adversarial_review.json"

        # Stage 1 Tier A: only run the optimizer if structured findings
        # exist. The prompt is dispatched off the findings JSON; without
        # it, the optimizer would have no signal and tends to invent
        # "improvements" (the draft_3 failure mode that fabricated CIs).
        if not findings_path.is_file():
            logger.warning(
                f"Adversarial findings JSON missing at {findings_path}; "
                "skipping Phase 4 optimizer (subtraction-only mode requires "
                "the structured findings file)."
            )
            # Stage 2 Tier D: even when optimizer is skipped, run
            # supplementary_pool — the holistic_draft may have emitted
            # [NEEDS CITATION] markers directly (Tier E discipline).
            self.advance_phase("supplementary_pool")
            return

        if prompt_path.exists():
            user_prompt = f"""
Apply subtraction-only optimizations per the system prompt.
- ASSEMBLED_PATH: {self.draft_dir / 'manuscript.md'}
- REVIEW_FINDINGS_PATH: {findings_path}
- REPORT_PATH: {self.project_dir / 'REPORT.md'}
- CLAIM_INVENTORY_PATH: {self.draft_dir / 'claim_inventory.tsv'}
- CITATION_POOL_PATH: {self.draft_dir / 'citation_pool.json'}
- OPTIMIZATION_LOG_PATH: {self.draft_dir / 'audit' / 'optimization_applied.md'}

Forbidden: adding any numeric value, CI, p-value, or citation that
does not appear verbatim in REPORT.md or claim_inventory.tsv. See the
system prompt's "Inviolable forbidden actions" section.
"""
            # Snapshot manuscript BEFORE optimizer to enable the
            # post-check that detects whether the optimizer regressed
            # by inventing new numerics.
            assembled_path = self.draft_dir / "manuscript.md"
            pre_text = (
                assembled_path.read_text(encoding="utf-8")
                if assembled_path.is_file()
                else ""
            )

            # Stage 1 Tier D: cost-tracking helper.
            rc, stdout, stderr, cost = await self._run_claude_p_with_cost(
                phase_label="phase_optimize",
                system_prompt_text=prompt_path.read_text(encoding='utf-8'),
                user_prompt=user_prompt,
                model=self.model,
            )
            if rc != 0:
                logger.error(
                    f"Optimizer failed:\nSTDOUT: {stdout}\nSTDERR: {stderr}"
                )

            # Stage 1 Tier A post-check: did the optimizer add any
            # numeric that wasn't in the pre-optimizer manuscript AND
            # isn't in REPORT.md? If so, the optimizer regressed —
            # log it so a human can audit.
            self._post_check_optimizer_subtraction(pre_text)

        # Stage 2 Tier D: optimize → supplementary_pool (was: → compliance_gate).
        # The optimizer's citation_reality dispatch inserts
        # [NEEDS CITATION] markers; supplementary_pool resolves them.
        self.advance_phase("supplementary_pool")

    def _post_check_optimizer_subtraction(self, pre_text: str) -> None:
        """Stage 1 Tier A self-defense.

        Compare the manuscript before and after the optimizer. Any new
        numeric (regex `\\d+(?:\\.\\d+)?`) that appears in the post
        version but NOT in pre AND NOT in REPORT.md is a candidate
        fabrication. Write the diagnostic to
        `audit/optimizer_subtraction_check.json`. Don't abort — humans
        decide. The point is to surface the regression cleanly rather
        than silently ship a worsened draft (the draft_3 failure mode).
        """
        import re
        import json
        assembled_path = self.draft_dir / "manuscript.md"
        report_path = self.project_dir / "REPORT.md"
        audit_dir = self.draft_dir / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)

        if not assembled_path.is_file():
            return
        post_text = assembled_path.read_text(encoding="utf-8")
        report_text = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""

        numeric_re = re.compile(r"\b\d+(?:\.\d+)?\b")
        pre_numerics = set(numeric_re.findall(pre_text))
        post_numerics = set(numeric_re.findall(post_text))
        new_numerics = post_numerics - pre_numerics

        suspect = sorted(
            n for n in new_numerics
            if n not in report_text
            # Filter trivial single-digits (1, 2, 3, etc.) that are
            # likely sentence-rewrite artifacts ("split into 3 parts"),
            # not data-claim fabrications.
            and len(n) >= 3
        )

        diagnostic = {
            "phase": "optimize",
            "new_numerics_count": len(new_numerics),
            "suspect_count": len(suspect),
            "suspect": suspect[:50],
            "note": (
                "Numerics present in post-optimizer manuscript but not "
                "in pre-optimizer manuscript and not in REPORT.md. "
                "Likely fabrications. If list is non-empty, audit the "
                "optimizer output before shipping."
            ),
        }
        (audit_dir / "optimizer_subtraction_check.json").write_text(
            json.dumps(diagnostic, indent=2), encoding="utf-8"
        )
        if suspect:
            logger.warning(
                f"Optimizer post-check: {len(suspect)} suspect new "
                f"numerics may be fabricated. First few: {suspect[:5]}. "
                f"See audit/optimizer_subtraction_check.json."
            )
        else:
            logger.info(
                "Optimizer post-check passed: no suspect new numerics."
            )

    async def phase_compliance_gate(self):
        logger.info("Running compliance gate (M6)...")
        assembled_path = self.draft_dir / "manuscript.md"
        content = assembled_path.read_text(encoding="utf-8") if assembled_path.exists() else ""
        
        errors = []
        if "Drafted by BERIL" not in content and "AI" not in content:
            errors.append("Missing AI Disclosure")
        if "Data Availability" not in content and "data are available" not in content.lower():
            errors.append("Missing Data Availability statement")
            
        if errors:
            logger.error(f"Compliance gate failed: {errors}. Triggering autofix...")
            errors_path = self.draft_dir / "compliance_errors.json"
            import json
            errors_path.write_text(json.dumps(errors), encoding="utf-8")
            
            prompt_path = Path(__file__).parent / "skill" / "prompts" / "compliance_fix.v1.md"
            if prompt_path.exists():
                user_prompt = f"""
Fix these compliance errors.
- ASSEMBLED_PATH: {assembled_path}
- COMPLIANCE_ERRORS_PATH: {errors_path}
"""
                cmd = [
                    self.claude_bin, "-p",
                    "--system-prompt", prompt_path.read_text(encoding='utf-8'),
                    "--dangerously-skip-permissions",
                    user_prompt
                ]
                env = self._isolate_env()
                proc = await asyncio.create_subprocess_exec(
                    *cmd, cwd=str(self.draft_dir), env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()
                
            content = assembled_path.read_text(encoding="utf-8")
            if "[USER REVIEW REQUIRED]" in content:
                logger.warning("Compliance autofix requires user clarification. Escalating.")
                raise RuntimeError("Compliance autofix requires user clarification.")

        # Stage 1 Tier B: advance to assemble (markdown → docx), not
        # directly to assembled. Previously the orchestrator jumped
        # straight to "assembled" without ever rendering the docx.
        self.advance_phase("assemble")

    def _stage_figures_for_assemble(self) -> None:
        """Stage 3 Tier A (2026-05-12): make `<project>/figures/`
        reachable as `<draft_dir>/figures/` so assemble_docx.py's
        relative-path resolution finds the canonical figures.

        Context: `assemble_docx.py` resolves image paths against
        `manuscript.md.parent` and REJECTS any path containing `..`
        (defensive against path traversal). The figures inventory
        contract has the LLM emit `figures/X.png` (relative). Without
        staging, those paths resolve to `<draft_dir>/figures/X.png`
        which doesn't exist — every figure renders as
        `[FIGURE MISSING: ...]`.

        Latent failure mode: pre-v0.7.x, the LLM happened to wrap
        image markdown in blockquotes (`> ![...]`) which the parser
        silently treats as blockquote text, not an image block —
        zero warnings, zero embeds. Surfaced on draft_9 when LLM
        emitted bare image-block form.

        Strategy: symlink the directory (cheap, in-tree). Fall back
        to copy when symlink fails (Windows; cross-volume mounts).
        Idempotent: re-runs leave a correct staged link alone.
        """
        project_figures = (self.project_dir / "figures").resolve()
        staged = self.draft_dir / "figures"

        if not project_figures.is_dir():
            logger.warning(
                f"stage_figures: {project_figures} does not exist; "
                "skipping (renderer will warn per missing figure)."
            )
            return

        # Idempotency: leave a correct existing symlink alone.
        if staged.is_symlink():
            try:
                if staged.resolve() == project_figures:
                    logger.info(
                        f"stage_figures: {staged} already symlinked to "
                        f"{project_figures}; reusing."
                    )
                    return
            except OSError:
                pass
            # Broken or wrong-target symlink: replace it.
            staged.unlink()
        elif staged.is_dir():
            # Stage 3 Tier J.1 (2026-05-15): defer to a real directory
            # only when it actually contains content. An empty directory
            # is almost always a side effect of an earlier phase
            # (`extract_figures.py` runs with `--output-dir <draft_dir>`
            # and creates an empty `figures/` as part of its scaffolding);
            # deferring to it makes Tier A a no-op and the renderer
            # then warns `image file not found` for every figure.
            # Observed on draft_1 (2026-05-15 ibd_phage_targeting run):
            # 14 figures, all WARN, docx shipped with no embedded media.
            if any(staged.iterdir()):
                logger.info(
                    f"stage_figures: {staged} is a real non-empty "
                    "directory; leaving in place (assumed user-managed)."
                )
                return
            logger.info(
                f"stage_figures: {staged} exists but is empty "
                "(likely an extract-phase side effect); removing and "
                "staging the project's figures dir."
            )
            staged.rmdir()
            # fall through to symlink/copy creation below
        elif staged.exists():
            # Some other file at that path — refuse to clobber.
            raise RuntimeError(
                f"stage_figures: {staged} exists but is neither symlink "
                "nor directory; cannot stage figures."
            )

        # Symlink first; fall back to copy on failure.
        try:
            staged.symlink_to(project_figures, target_is_directory=True)
            logger.info(
                f"stage_figures: symlinked {staged} -> {project_figures}"
            )
            return
        except (OSError, NotImplementedError) as exc:
            logger.warning(
                f"stage_figures: symlink failed ({exc!r}); "
                "falling back to copy."
            )

        # Copy fallback.
        import shutil
        shutil.copytree(project_figures, staged)
        count = sum(1 for _ in staged.iterdir())
        logger.info(
            f"stage_figures: copied {count} entries to {staged}"
        )

    async def phase_assemble(self):
        """Stage 1 Tier B: Phase 8 — markdown → docx via assemble_docx.py.

        SPEC §3 line 160 + §12. Previously not wired into run_pipeline;
        the pipeline ended at the `assembled` state but never produced
        a .docx file. This method invokes the existing
        skill/tools/assemble_docx.py to render manuscript.md to
        manuscript.docx.

        Stage 3 Tier A (2026-05-12): the renderer resolves image paths
        relative to `manuscript.md.parent` and rejects any path
        containing `..`. The LLM emits `figures/X.png` (per inventory
        contract), so the figures directory must be reachable as a
        sibling of manuscript.md. We stage the canonical figures dir
        (<project>/figures) into <draft_dir>/figures via symlink
        (falling back to copy when symlink fails — e.g., cross-volume,
        Windows). Idempotent: re-runs leave the staged link alone.
        """
        logger.info("Running assemble phase (markdown → docx)...")
        manuscript_md = self.draft_dir / "manuscript.md"
        manuscript_docx = self.draft_dir / "manuscript.docx"

        if not manuscript_md.is_file():
            logger.error(
                f"assemble: manuscript.md not found at {manuscript_md}; "
                "cannot render docx. Pipeline cannot complete."
            )
            raise RuntimeError(
                f"manuscript.md missing at {manuscript_md}"
            )

        # Stage 3 Tier A: stage <project>/figures/ as a sibling of
        # manuscript.md so `figures/X.png` resolves against draft_dir.
        self._stage_figures_for_assemble()

        tools_dir = Path(__file__).parent / "skill" / "tools"
        cmd = [
            sys.executable,
            str(tools_dir / "assemble_docx.py"),
            str(manuscript_md),
            str(manuscript_docx),
        ]
        audit_dir = self.draft_dir / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        with open(audit_dir / "assemble_docx.log", "wb") as logf:
            logf.write(stdout)
            logf.write(stderr)
        if proc.returncode != 0:
            logger.error(
                f"assemble_docx.py exited {proc.returncode}; "
                f"stderr: {stderr.decode('utf-8', errors='ignore')[:500]}"
            )
            raise RuntimeError(
                "assemble_docx.py failed; see audit/assemble_docx.log"
            )

        if not manuscript_docx.is_file():
            raise RuntimeError(
                f"assemble_docx.py exited 0 but {manuscript_docx} was not "
                "produced. Renderer contract violated."
            )

        logger.info(
            f"Wrote {manuscript_docx} "
            f"({manuscript_docx.stat().st_size} bytes)"
        )
        self.advance_phase("assembled")

