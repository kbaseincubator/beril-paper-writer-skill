# Prior-Art Scan: Automated Scientific Paper Writing

**Source:** subagent-generated 2026-04-25 from local copies under
`repos-for-analysis/` plus `.auto-memory/project_competitive_analysis.md`.
PaperQA2 fetch was attempted but is documented from memory only here, not
re-verified.

**How this document is used:** The "Patterns to ADOPT" lists below are NOT
all adopted as-is. SPEC §6 and DECISIONS.md record which patterns made it
into v0.1 vs which were deferred or rejected. Below the per-system reports,
a **Critical commentary on the subagent's recommended architecture** section
documents specific places where the subagent's auto-recommendations would
have over-stepped Adam's scoping decisions. Read that section before
treating this document as a build spec.

---

## 1. claude-scientific-writer

**What it does:** A production Python package (727 lines of api.py) that combines real-time literature search (Perplexity Sonar Pro) with AI-driven paper generation. Uses Claude Agent SDK for agentic execution with three effort levels (Haiku/Sonnet/Sonnet). Produces publication-ready PDFs, LaTeX, and markdown. Operates as a Claude Code plugin, CLI, or Python package.

**Patterns to ADOPT:**
- **ADOPT-CS-1: Effort-level stratification** (`api.py:74-76`, EFFORT_LEVEL_MODELS dict). Allows graceful degradation: low effort → Haiku (cost), medium/high → Sonnet (quality). Applies same prompt to all tiers; variation is model selection only.
- **ADOPT-CS-2: Auto-continuation stop hook** (`api.py:37-64`, `create_completion_check_stop_hook`). Forces agent to continue rather than stopping prematurely. Boolean `auto_continue=True` drives `continue_=True` returns. Prevents agent from quitting mid-paper.
- **ADOPT-CS-3: Data-aware context injection** (`core.py`, `create_data_context_message`). Prepares CSV/image files as structured messages before passing to agent. Files are explicitly enumerated so agent knows what data exists.
- **ADOPT-CS-4: Output directory management** (ensure_output_folder + working directory setup). Centralizes all outputs (PDFs, LaTeX, markdown) in a single location with consistent naming.

**Patterns to IMPROVE:**
- **IMPROVE-CS-1: Citation hallucination risk.** The system uses Perplexity Sonar Pro for literature search but does not verify that citations map to real papers before inserting them into the PDF. Papers could cite non-existent sources. Need: citation verification loop (check DOI, PMID, or page crawl before inclusion).
- **IMPROVE-CS-2: No throughline-consistency check.** Sections are written independently; no agent checks whether claim in Methods contradicts claim in Results, or whether Discussion references a methods detail that was never stated. Add a final "narrative coherence" pass.

**Patterns to SKIP:**
- **SKIP-CS-1: Monolithic 727-line api.py.** All logic (research, writing, PDF generation) lives in one file. For BERIL-writer, we need per-section agents + a composition layer. Monolithic architecture makes role-based constraints (e.g., "research agent sees only search tools, writing agent sees only LLM") impossible to enforce.

**Risks they hit:**
- Citation hallucination (literature search claims sources that don't exist or are misattributed).
- Inconsistency across sections (no final coherence pass).
- No human-in-the-loop for validation of key claims before PDF generation.

---

## 2. AI-Scientist-v2 (Sakana)

**What it does:** A 4-week research pipeline (ideation → experiments → writing) using agentic tree search. The writeup phase (`perform_writeup.py`, 810 lines) generates LaTeX manuscripts from experiment results. Uses Semantic Scholar for novelty-checking during ideation, then LLM-writes methods/results sections and compiles PDFs via pdflatex + bibtex. Designed for ML research but generalizable.

**Patterns to ADOPT:**
- **ADOPT-AS-1: Template-constrained writing** (`blank_icml_latex/`, `blank_icbinb_latex/`). Pre-built LaTeX structure (introduction, methods, results, discussion, conclusion, references) ensures sections go into correct slots. Agent fills in content without worrying about structure.
- **ADOPT-AS-2: Citation key extraction and deduplication** (`perform_writeup.py`, line 370+). System extracts all `\cite{}` commands from generated LaTeX, deduplicates them, and builds a `references.bib` file. Prevents citation ID collisions and broken references.
- **ADOPT-AS-3: Multi-pass LaTeX compilation** (`compile_latex()` function, line 48-70). Runs pdflatex → bibtex → pdflatex → pdflatex to ensure cross-references and citations are resolved. Catches LaTeX errors early.
- **ADOPT-AS-4: Page-break detection for impact statements** (`detect_pages_before_impact()`, line 71+). Compiles temporary copy of LaTeX, detects page number where "Impact Statement" appears, then validates page count constraint. Ensures submission requirements are met before finalizing.

**Patterns to IMPROVE:**
- **IMPROVE-AS-1: No section-by-section validation.** The system writes all LaTeX at once, then compiles. If results section is poorly written, you don't discover this until the full PDF is built. Need: per-section compilation + validation loop.
- **IMPROVE-AS-2: Citation management is brittle.** The code parses citation commands via regex (`remove_accents_and_clean()`, line 21-32) and rebuilds the bib file, but if an LLM-written citation command has a typo (e.g., `\cite{Smith2023a}` but no matching entry), bibtex silently renders `[?]`. No fallback to Semantic Scholar for missing citations.

**Patterns to SKIP:**
- **SKIP-AS-1: Heavy LaTeX dependency.** AI-Scientist requires pdflatex, bibtex, and various LaTeX packages to be pre-installed on the system. For BERIL-writer, we should support markdown-first with optional LaTeX export, not LaTeX as the canonical format.

**Risks they hit:**
- LaTeX compilation failures when pdflatex or bibtex are not available or have incompatible versions.
- Citation key collisions in bibtex.
- Silent failures when citation keys don't exist in the bib file (bibtex renders `[?]` without error).
- No per-section validation before final PDF generation.

---

## 3. ScienceClaw

**What it does:** A protocol-driven research agent (600 lines in `SCIENCE.md` instruction set) that executes "Research Recipes" (6-step pipelines like gene-landscape analysis). Outputs structured reports with markdown + figures + METHODS.md. Uses OpenClaw (agentic layer) + Claude Backend. No hard-coded paper structure; instead, recipes are declarative workflows (search → query DB → run analysis → write report).

**Patterns to ADOPT:**
- **ADOPT-SC-1: Recipe-driven decomposition.** Each research recipe is a named workflow (gene-landscape, drug-target, pathway-analysis, etc.). Users invoke `/analyze <topic>`, system matches it to a recipe and executes the pipeline. Recipes encode domain knowledge, not hardcoded in Python. For BERIL-writer: define paper-generation recipes (e.g., "methods-first" for experimental papers vs. "review-first" for surveys).
- **ADOPT-SC-2: METHODS.md generation as mandatory output.** After task completion, system generates a detailed Methods section (data sources, search strategy, software versions, statistical methods, sample sizes) suitable for direct insertion into a paper. This is SEPARATE from the narrative report. For BERIL-writer: always produce METHODS.md alongside the manuscript.
- **ADOPT-SC-3: Substantive progress signals.** The instruction set requires every progress message to include "at least one concrete number, fact, or intermediate result" (line 66-80). Prevents silent work. Examples: "PubMed 检索到 47 篇文献" (47 papers found), "TCGA 数据下载完成（3.2MB, 438 样本）" (3.2MB, 438 samples). For BERIL-writer: report actual counts of figures generated, citations verified, sections drafted.
- **ADOPT-SC-4: Quick vs. deep task classification.** System classifies incoming queries as "quick" (single lookup, one figure) or "deep" (multi-step analysis, full report). Quick tasks respond directly in chat; deep tasks create project directories and full workflows. For BERIL-writer: quick = single section revision; deep = full paper generation.
- **ADOPT-SC-5: No refuse policy + honesty constraint.** The system is instructed to "do whatever the user asks" but "never say 'I can't'" while maintaining that "Never say 'I'm unable to'". Paradoxically, it also says "Be direct, precise, and honest." This enforces task completion but within ethical bounds. For BERIL-writer: define which paper-writing requests are in-scope (e.g., methods for computational studies: yes; methods for human trials: require ethics approval notice).

**Patterns to SKIP:**
- **SKIP-SC-1: Chinese-language-first design.** The SCIENCE.md instructions are bilingual (Chinese+English) and optimized for Chinese users ("DeepSeek 更便宜"). This is domain-specific and not transferable to BERIL.

**Risks they hit:**
- The "no refuse" policy creates liability if a user asks to write unethical manuscripts (e.g., forging data, plagiarizing). System is instructed NOT to refuse, which violates academic integrity.
- Recipe matching is heuristic; if a user's question doesn't match a pre-defined recipe, the system falls back to ad-hoc agent behavior, which is less reliable.

---

## 4. open-coscientist (Jataware)

**What it does:** A LangGraph-based multi-agent system (8-10 specialized agents) for hypothesis generation, not paper writing per se. But the architecture is instructive: Supervisor → Literature Review (MCP-backed) → Generate → Reflection → Review → Rank → Tournament (Elo) → Meta-Review → Evolve → Proximity (deduplication). Each node is an independent agent with specific responsibilities.

**Patterns to ADOPT:**
- **ADOPT-OC-1: MCP-backed literature review.** The system has an optional MCP server that provides access to PubMed, Google Scholar, NVD, and other sources. Literature queries are delegated to the MCP, not hallucinated by the LLM. For BERIL-writer: integrate MCP for literature search (via existing BERIL infrastructure or external APIs like PubMed).
- **ADOPT-OC-2: Per-node streaming and state management.** Each LangGraph node returns structured output (hypotheses, reviews, rankings). State flows from node to node; each node sees only what it needs. For BERIL-writer: define per-section state (e.g., "Abstract" node outputs abstract + key claims; "Methods" node inputs key claims + experimental design).
- **ADOPT-OC-3: Elo-tournament for comparative ranking.** Rather than absolute scores, the system runs pairwise comparisons between hypotheses and assigns Elo ratings. This is more robust than asking an LLM "rank these on a scale of 1-10." For BERIL-writer: use Elo ranking for section variants (generate 3 methods sections, tournament to pick the best).
- **ADOPT-OC-4: Deduplication via clustering.** The Proximity node uses semantic similarity to cluster and remove high-similarity duplicates. For BERIL-writer: after section generation, cluster similar sections and keep the most polished variant.
- **ADOPT-OC-5: Adaptive strategy selection based on context.** The Review node uses "comparative batch for ≤5 items, parallel for >5 items" (line in architecture table). For BERIL-writer: if you have 3 citation candidates for a claim, batch-review them; if 50, parallelize.

**Patterns to IMPROVE:**
- **IMPROVE-OC-1: No guarantee of section coherence.** Hypotheses are ranked independently; the system doesn't check whether hypothesis A's assumptions align with hypothesis B's methods. For paper writing, you need explicit coherence checks across sections.
- **IMPROVE-OC-2: Reflection is optional and MCP-dependent.** If MCP is not available, the Reflection node skips literature comparison. Papers generated without this pass may cite non-existent work or miss novelty checks.

**Patterns to SKIP:**
- **SKIP-OC-1: Hypothesis-focused architecture.** open-coscientist is designed to generate and rank hypotheses, not to write manuscripts. Trying to adapt it for paper-writing would require significant refactoring (each hypothesis is ~50 words; a methods section is ~500 words).

**Risks they hit:**
- MCP availability is optional; if not running, system reverts to LLM-only literature review (hallucination risk).
- Elo tournament requires >1 item to run; if only 1 variant is generated, ranking is skipped.
- Reflection node is recommended but optional; user may skip it, and hypothesis quality suffers.

---

## 5. scientific-agent-skills (K-Dense)

**What it does:** A library of 134 domain-specific skills (not a paper-writing system itself, but a toolkit). The `scientific-writing` skill (`SKILL.md`, ~400 lines) defines best practices for IMRAD structure, citation styles, reporting guidelines, and visual generation requirements.

**Patterns to ADOPT:**
- **ADOPT-SAS-1: Two-stage writing process (outline → prose).** The skill explicitly mandates: "Create section outlines with key points using research-lookup, THEN convert to flowing prose" (line 32). Never submit bullet points. For BERIL-writer: draft all sections as outlines first (atomic claims + evidence), then pass through a prose-generation agent.
- **ADOPT-SAS-2: Mandatory visual generation.** The skill requires "EVERY scientific paper MUST include a graphical abstract plus 1-2 additional figures" (line 48). Minimum figure counts are specified by document type (research paper: 5-8 figures). For BERIL-writer: integrate figure generation into the pipeline, not as an afterthought.
- **ADOPT-SAS-3: Reporting guidelines enforcement.** The skill lists CONSORT (trials), STROBE (observational), PRISMA (reviews) and requires documents to follow the appropriate checklist. For BERIL-writer: given a paper type, enforce the relevant reporting guideline.
- **ADOPT-SAS-4: Citation style flexibility.** Support APA, AMA, Vancouver, Chicago, IEEE. The skill lists examples for each. For BERIL-writer: allow users to specify citation style upfront, then enforce it throughout.
- **ADOPT-SAS-5: Visual enhancement is MANDATORY, not optional.** The skill says "⚠️ MANDATORY" three times in the first 50 lines of the figures section. This design choice prevents papers without visuals from being considered complete. For BERIL-writer: fail the generation if no figures are produced.

**Patterns to IMPROVE:**
- **IMPROVE-SAS-1: Visual generation is delegated to sub-skills (scientific-schematics, generate-image).** The scientific-writing skill doesn't generate figures itself; it delegates to external skills. This is correct, but the BERIL-writer needs to orchestrate these calls explicitly. The skill docs say "when in doubt, generate a figure," but don't explain the decision logic (e.g., should you generate a figure for every paragraph or only for complex concepts?).

**Patterns to SKIP:**
- **SKIP-SAS-1: Skill-based modular design doesn't apply directly.** Scientific-agent-skills are designed for Cursor/Claude Code/Codex, which auto-load skills from a directory. BERIL-writer is a standalone skill, not a meta-system that orchestrates other skills. However, the *design principles* (two-stage writing, mandatory visuals, reporting guidelines) are absolutely transferable.

**Risks they hit:**
- No risk unique to this system; it's a best-practices guide, not an executable system.

---

## 6. K-Dense Agentic Data Scientist (Archived Reference, Checked via k-dense-analysis-2025-12.md)

**What it does:** A 9-agent multi-agent system with separated planning (Plan Maker → Plan Reviewer → Plan Parser) and execution phases (Coding Agent → Review Agent → Criteria Checker → Stage Reflector). Uses ADK (Agent Development Kit) orchestration. Key innovation: success criteria are central to control flow, not stage completion.

**Patterns to ADOPT:**
- **ADOPT-KDS-1: Separated planning and execution phases.** Phase 1 generates a plan (stages + success criteria); Phase 2 executes stages and checks criteria. For BERIL-writer: Plan phase could identify required sections + key claims + evidence sources; Execution phase could draft each section and validate against the plan.
- **ADOPT-KDS-2: Success criteria as primary control.** The system exits when "all criteria are met," not when "all stages are done." This is crucial: a poorly-written methods section still fails the criteria check (e.g., "Is the experimental design clearly described?"), forcing revision. For BERIL-writer: define per-section criteria (abstract: <250 words, 1 sentence per key finding; methods: reproducible, includes all parameters).
- **ADOPT-KDS-3: Stage Reflector for adaptive planning.** After each stage, the Stage Reflector updates remaining stages based on discoveries. For BERIL-writer: if Results discovers a novelty that wasn't mentioned in Methods, the Methods stage is flagged for revision.
- **ADOPT-KDS-4: LLM-driven event compression.** As execution runs, event history grows. The system compresses old events using an LLM (summarize 40+ events into 400-600 words), keeping only recent events uncompressed. For BERIL-writer: paper generation can produce hundreds of intermediate messages (citations checked, claims validated, figures generated); compression keeps the token budget manageable.
- **ADOPT-KDS-5: Loop detection and review confirmation.** A Review Agent gives feedback, but an explicit Review Confirmation agent decides whether to accept or reject the review, preventing infinite loops. For BERIL-writer: prevent the "revise → review → revise" loop from spinning forever by setting a max revision count or confidence threshold.

**Patterns to IMPROVE:**
- **IMPROVE-KDS-1: Criteria checking is LLM-based, not automated.** The Criteria Checker agent reads files and judges whether success criteria are met. This is soft (subjective) and can hallucinate. A tighter system would check hard criteria (word count via wc, reference count via regex) programmatically.

**Patterns to SKIP:**
- **SKIP-KDS-1: ADK dependency.** The system uses Google ADK (Agent Development Kit), which is a specific framework. BERIL-writer should use Claude Agent SDK directly, not wrap another orchestration layer.

**Risks they hit:**
- Context window management: even with compression, a long analysis can hit token limits.
- Stage Reflector can add infinite new stages if not constrained; conservative logic is needed.

---

## Synthesis: Recommended Architecture for BERIL Paper-Writer

### **Agent Decomposition**

**Phase 1: Planning** (30min)
- **Plan Agent** (reads project files + BERIL metadata) → outputs a structured plan:
  - Required sections (determined by paper type: research paper = IMRAD, review = narrative, etc.)
  - Key claims to establish (hypothesis, main findings, novelty)
  - Evidence requirements (which DB tables, which figures)
  - Citation targets (how many per section, types: primary research / review / methods)

**Phase 2: Execution** (2-4 hours)
- **Per-section agents** (one for each of: Abstract, Introduction, Methods, Results, Discussion, Conclusion)
  - Inputs: section template + key claims from Plan + evidence from BERIL (tables, figures)
  - Output: ~500-word section draft + inline claims + evidence IDs
  - Tools: BERIL query, literature search (MCP), figure generation

- **Citation Verification Agent** (single pass after all sections)
  - Inputs: all cite{} commands + evidence IDs
  - Outputs: verified bibliography + flag any unverified claims
  - Tools: DOI lookup, PMID lookup, BERIL schema navigation

- **Coherence Agent** (single pass after citation verification)
  - Inputs: all sections in order
  - Outputs: contradiction flags + throughline repairs
  - Example: "Results claims efficiency is 87%, but Methods says 'up to 85%'" → repair

- **Visual Generation Agent** (parallel with section writing)
  - Inputs: section drafts + data files
  - Outputs: graphical abstract + 1 figure per section
  - Tools: scientific-schematics (diagrams) + generate-image (photorealistic)

**Phase 3: Assembly & Output** (30min)
- **Formatter Agent** (LaTeX + markdown)
  - Inputs: all sections + bibliography + figures
  - Outputs: PDF + markdown + LaTeX
  - Enforces: reporting guidelines (CONSORT/STROBE/PRISMA), citation style, figure numbering

### **Gap-Fill Loop**

After each section is drafted:
1. Criteria Checker validates section (word count, claim count, evidence completeness)
2. If criteria not met, section agent revises
3. Max 2 revision rounds per section (then manual review required)

After all sections:
1. Citation Verification Agent checks all citations
2. If verification fails for >10% of citations, abort and report (require user to provide missing references)
3. Coherence Agent flags contradictions
4. If contradictions affect interpretation, Stage Reflector updates affected sections

### **Throughline-Selection Mechanism**

At Plan phase:
- Identify 3-5 key claims that span all sections (e.g., "Protein X increases fitness in soil drought")
- Trace each claim through: Abstract → Intro → Methods → Results → Discussion
- Assign a "claim ID" to each

At assembly phase (Coherence Agent):
- For each claim ID, verify the thread is unbroken
- Flag sections where claim is mentioned but evidence is missing
- Repair or escalate to manual review

### **Citation-Verification Flow**

1. **As written**: Section agents cite sources (but LLM may hallucinate)
2. **Immediate verification**: Citation Agent checks DOI/PMID/URL
   - If found: store canonical metadata
   - If not found: flag as unverified
3. **Before output**: Unverified citations are either:
   - Removed (if low confidence)
   - Replaced with MCP-sourced alternatives (if same claim available in PubMed)
   - Reported to user (if critical claim, require manual input)

### **Human-in-the-Loop Pattern**

- **Phase 1 gate**: User reviews Plan before execution (5min) — catches wrong paper type, missing data sources
- **Phase 2a gate** (optional): User reviews section drafts as they're generated (can skip for speed, accept all)
- **Phase 2b gate**: User reviews citation verification report (5min) — fix unverified claims
- **Phase 3 gate**: User reviews final PDF/markdown (15min) — final edits before submission

### **Section-Assembly Order**

Generate in this order to maximize information flow:
1. **Methods** (foundational; constrains Results format)
2. **Results** (depends on Methods being clear)
3. **Discussion** (interprets Results in context of Intro)
4. **Introduction** (sets up hypothesis; can be refined after Results are known)
5. **Abstract** (last; summarizes all sections)
6. **Conclusion** (if required; often same as final Discussion paragraph)

### **Key Design Constraints**

- **No hallucinated citations**: Citation Agent performs hard verification (DOI/PMID lookup)
- **No silent contradictions**: Coherence Agent explicitly flags every contradiction
- **No figure-less papers**: Visual generation is mandatory, not optional
- **No method-less papers**: METHODS.md is always generated, even if paper is unpublishable
- **Reproducibility first**: All DB queries, API calls, code versions logged in METHODS.md

---

## Key Risks Across All Prior Systems

1. **Citation hallucination** — ALL systems generate citations without verification. FIX: Implement hard DOI/PMID lookup before including in bibliography.

2. **Section-local optimization** — Sections are written independently; no agent checks whether Methods matches Results. FIX: Coherence Agent validates throughline after all sections drafted.

3. **Silent method invisibility** — Users focus on paper PDF but don't see how it was made. FIX: Always generate METHODS.md with full provenance (DB query, search strategy, software versions).

4. **No human-in-the-loop for high-stakes claims** — If paper claims a new biomarker, no verification that the claim is grounded in the data. FIX: Identify high-stakes claims in Plan phase; require user verification before inclusion.

5. **LaTeX brittle** — AI-Scientist v2 depends on pdflatex + bibtex; if they fail, entire pipeline fails. FIX: Markdown-first, with optional LaTeX export; never require LaTeX compilation for correctness.

6. **Figure generation is optional** — Most systems treat figures as "nice to have." FIX: Mandate figures; paper is incomplete without them (per scientific-agent-skills design).

7. **No reproducibility bundle** — Readers can't reproduce the analysis that led to the paper. FIX: Archive all queries, code, data snapshots in a reproducibility bundle (per open-coscientist / K-Dense patterns).

---

## Summary: BERIL Paper-Writer Differentiators

1. **BERIL-as-knowledge-source**: Unique access to K-BERDL (38 DBs, 293K genomes) means citations are automatically verifiable against BERIL schema.

2. **Event-driven architecture**: Unlike monolithic systems (claude-scientific-writer), use per-agent events with state accumulation. Easier to debug, extend, and parallelize.

3. **Success-criteria-driven execution**: Like K-Dense Agentic DS, define criteria per section and fail early if unmet. Don't generate a paper that passes "did it get written?" when it should fail "is it coherent?"

4. **Mandatory reproducibility**: Unlike competitors, always generate METHODS.md + full provenance. This is non-optional, not "nice to have."

5. **Hard citation verification**: Every citation → DOI/PMID lookup before inclusion. No hallucinated references.

---

## Critical commentary on the subagent's recommended architecture

The subagent's "Recommended Architecture for BERIL Paper-Writer" section
above (lines ~144–239) was generated before being checked against Adam's
scoping decisions. Several recommendations would over-step those decisions
and are **not adopted** in v0.1:

- **"Visual Generation Agent (parallel with section writing)"** — REJECTED.
  v0.1 reuses existing project figures only; no regeneration. Missing
  figures become explicit gap-fill requests. See DECISIONS D-004.
- **"Mandatory graphical abstract"** — REJECTED. ICMJE does not require
  graphical abstracts; mandating one for BERIL papers is overreach. The
  pattern came from K-Dense scientific-writing skill, which targets a
  different audience. See SPEC §1.2.
- **"Elo-tournament for section variants (generate 3 methods sections,
  rank, pick best)"** — DEFERRED. Useful idea but heavy for v0.1; revisit
  if section quality is consistently poor. See DECISIONS open questions.
- **"LLM-driven event compression for long event histories"** — DEFERRED.
  Premature optimization for v0.1. The sister skill beril-adversarial
  has not needed this; paper-writer probably doesn't either at the start.
- **"Phase 2a gate: User reviews section drafts as they're generated"**
  — DEFERRED. Each user gate adds latency and friction. v0.1 has
  three gates (throughline pick, gap-fill response, review acceptance);
  more would be excessive. Power users can re-invoke `continue` between
  sections if they want fine-grained review.

Patterns from the subagent that ARE adopted:

- Per-section subagent decomposition (SPEC §6.2, citing AI-Scientist-v2)
- Section assembly order Methods → Results → Discussion → Intro → Abstract
  (SPEC §6.1, DECISIONS D-012, citing ScienceClaw + scientific-agent-skills)
- Hard citation verification with DOI/PMID lookup (SPEC §6.4, DECISIONS
  D-009, citing every prior system)
- Plan/Execute/Assemble phase separation (SPEC §6, citing K-Dense
  Agentic DS)
- METHODS.md grounding with full provenance (SPEC §6.3, DECISIONS D-003,
  citing ScienceClaw)
- Throughline coherence check (SPEC §4.3, partially mechanized via
  reframer.v1.md prompt; deeper coherence-agent deferred)
- Markdown-first with optional .docx export (SPEC §1.2, DECISIONS D-001,
  citing AI-Scientist-v2's brittle LaTeX dependency as anti-pattern)
- Hard cap on rewrite passes (SPEC §8.3, DECISIONS D-006, citing K-Dense
  loop-detection pattern)
- Loose coupling to adversarial review (SPEC §8.1, DECISIONS D-005,
  derived from absence in prior systems)

The subagent's "Key Risks Across All Prior Systems" (lines 245–272) is
useful as-is and reflects the actual failure modes the writer must defend
against. SPEC §2 design premises were partly informed by that list.
