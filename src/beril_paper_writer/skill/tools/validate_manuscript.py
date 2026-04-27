#!/usr/bin/env python3
"""validate_manuscript.py — M1-M10 mechanized validators per SPEC §7.1.

Standalone script invoked by the shell orchestrator:

    python3 "$SKILL_DIR/tools/validate_manuscript.py" <draft_dir> [--mode paper|report]

Outputs a JSON ValidationReport to stdout. Exit code 0 if all
validators pass or are not-applicable; 1 if any hard validator fails;
0 with non-zero soft-warning count is still 0 (caller decides whether
to escalate).

Per SPEC §7.1.1 each validator failure carries an `escalation_path`
(`auto-fix | escalate | user-modify | accept-as-limitation`) the
orchestrator uses to decide what to do next. Per §7.1.2 M5 is
implemented as a soft-warning only in v0.1.

The script can be imported as a module for unit testing; individual
validator functions are pure (text in, ValidatorResult out).

Validator inputs:
  - For draft directories with per-section files
    (00_throughline.md, 01_methods.md, ...): each file is treated as
    a section; M1's section-presence check works on the per-file set.
  - For draft directories with a single manuscript.md: the markdown
    is split by ATX headers (#, ##, ...) into a section dict; same
    validators run.

Either layout works; the orchestrator decides which to use depending
on phase. Tests cover both.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants — section name aliases per SPEC §3.2 and §3.2.2
# ---------------------------------------------------------------------------

# IMRAD sections required in --mode paper. Each entry is (canonical name,
# accepted aliases). Match is case-insensitive on the section header text.
PAPER_REQUIRED_SECTIONS = {
    "title": ("title", "title page"),
    "abstract": ("abstract", "summary"),
    "introduction": ("introduction", "background and significance"),
    "methods": (
        "methods", "materials and methods", "materials & methods",
        "methodology", "experimental methods",
    ),
    "results": ("results", "findings"),
    "discussion": ("discussion",),
    "references": ("references", "bibliography", "citations", "works cited"),
}

# REPORT-mode section structure per SPEC §3.2.2.
REPORT_REQUIRED_SECTIONS = {
    "project_summary": ("project summary", "summary"),
    "background": ("background and question", "background", "question"),
    "what_was_done": ("what was done", "what was done (methods)", "methods"),
    "what_was_observed": (
        "what was observed", "what was observed (findings)", "findings", "results",
    ),
    "observations": (
        "observations and open questions", "observations", "open questions",
    ),
    "limitations": ("limitations and caveats", "limitations", "caveats"),
    "next_steps": ("next steps", "future work"),
}

# Subsections expected within a structured Abstract (M2). Match is fuzzy.
ABSTRACT_SUBSECTIONS = {
    "background": ("background", "objective", "background/objective", "aim"),
    "methods": ("methods",),
    "results": ("results", "findings"),
    "conclusions": ("conclusions", "conclusion"),
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

VALID_STATUSES = ("pass", "fail", "soft-warning", "not-applicable")
VALID_ESCALATION_PATHS = (
    "auto-fix",
    "escalate",
    "user-modify",
    "accept-as-limitation",
)


@dataclass
class Violation:
    """One specific violation identified by a validator."""

    severity: str  # "error" | "warning"
    section: str   # section name where the violation lives, or "(global)"
    line: Optional[int]  # 1-based line in the source file, if locatable
    message: str
    escalation_path: str  # one of VALID_ESCALATION_PATHS

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class ValidatorResult:
    """One validator's outcome."""

    id: str          # "M1" .. "M10"
    name: str        # short human-readable name
    status: str      # one of VALID_STATUSES
    violations: list[Violation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "violations": [v.to_dict() for v in self.violations],
        }


@dataclass
class ValidationReport:
    """The full report from running all validators against a draft."""

    draft_dir: str
    mode: str  # "paper" | "report"
    validators: list[ValidatorResult]

    def to_dict(self) -> dict:
        passed = sum(1 for v in self.validators if v.status == "pass")
        failed = sum(1 for v in self.validators if v.status == "fail")
        soft = sum(1 for v in self.validators if v.status == "soft-warning")
        na = sum(1 for v in self.validators if v.status == "not-applicable")
        if failed > 0:
            overall = "fail"
        elif soft > 0:
            overall = "warn"
        else:
            overall = "pass"
        return {
            "draft_dir": self.draft_dir,
            "mode": self.mode,
            "validators": [v.to_dict() for v in self.validators],
            "summary": {
                "passed": passed,
                "failed": failed,
                "soft_warnings": soft,
                "not_applicable": na,
                "overall_status": overall,
            },
        }

    @property
    def overall_status(self) -> str:
        return self.to_dict()["summary"]["overall_status"]


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------

# ATX header regex: matches lines starting with 1-6 '#' followed by space.
# Captures the level (number of #) and the title text.
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)


def split_into_sections(text: str) -> dict[str, str]:
    """Split markdown into sections by H1/H2 headers.

    Returns a dict {normalized_section_name: section_text}. Section text
    includes the header line and everything up to the next H1/H2.
    Higher-level headers (H3+) are kept inside their parent section.

    Section names are lowercased and stripped. The dict insertion order
    preserves document order, useful for IMRAD ordering checks.
    """
    sections: dict[str, str] = {}
    lines = text.split("\n")
    current_name = ""  # text before the first header
    current_lines: list[str] = []

    def flush() -> None:
        if current_lines or current_name:
            existing = sections.get(current_name, "")
            joined = "\n".join(current_lines)
            sections[current_name] = (existing + "\n" + joined).strip() if existing else joined

    for line in lines:
        m = re.match(r"^(#{1,2})\s+(.+?)\s*#*\s*$", line)
        if m:
            # Flush previous section
            flush()
            current_name = m.group(2).strip().lower()
            current_lines = [line]
        else:
            current_lines.append(line)
    flush()
    return sections


def section_matches_alias(section_name: str, aliases: tuple[str, ...]) -> bool:
    """Case-insensitive whole-string match against any alias."""
    name = section_name.strip().lower()
    return any(name == a.lower() for a in aliases)


def find_section(
    sections: dict[str, str], aliases: tuple[str, ...]
) -> Optional[str]:
    """Return the section text matching any alias, or None."""
    for name, text in sections.items():
        if section_matches_alias(name, aliases):
            return text
    return None


def section_word_count(text: str) -> int:
    """Approximate word count, skipping the header line."""
    body_lines = [l for l in text.split("\n") if not l.startswith("#")]
    body = " ".join(body_lines)
    return len(body.split())


# ---------------------------------------------------------------------------
# Manuscript loader (handles both single-file and per-section layouts)
# ---------------------------------------------------------------------------

# Per-section files in IMRAD order, per LAYOUT.md output routing.
# 07_data_availability.md exists because ICMJE requires a dedicated
# data-availability statement (M4 validator checks for it).
SECTION_FILES_IN_ORDER = [
    "00_throughline.md",
    "01_methods.md",
    "02_results.md",
    "03_discussion.md",
    "04_introduction.md",
    "05_abstract.md",
    "06_limitations.md",
    "07_data_availability.md",
    "references.md",
]


def load_manuscript_sections(draft_dir: Path) -> dict[str, str]:
    """Load manuscript content as a section dict.

    Strategy:
    - If `manuscript.md` exists, parse it via split_into_sections.
    - Else look for per-section files (01_methods.md, ...) and treat each
      as a section (filename stem → section name; file content as section text).
      The actual section header inside the file is preferred when present.

    The returned dict's keys are normalized lowercase section names.
    """
    manuscript_md = draft_dir / "manuscript.md"
    if manuscript_md.is_file():
        text = manuscript_md.read_text(encoding="utf-8")
        return split_into_sections(text)

    # Per-section layout
    sections: dict[str, str] = {}
    for fname in SECTION_FILES_IN_ORDER:
        f = draft_dir / fname
        if not f.is_file():
            continue
        content = f.read_text(encoding="utf-8")
        # Try to find an H1/H2 header inside; if present, use it as the
        # section name. Otherwise infer from the filename stem.
        m = re.search(r"^(#{1,2})\s+(.+?)\s*#*\s*$", content, re.MULTILINE)
        if m:
            name = m.group(2).strip().lower()
        else:
            # 01_methods.md → methods
            stem = f.stem
            name = re.sub(r"^\d+_", "", stem).replace("_", " ").lower()
        sections[name] = content
    return sections


# ---------------------------------------------------------------------------
# Helpers shared across validators
# ---------------------------------------------------------------------------

# In-prose citation pattern: matches [N], [N,M], [N-M], [N, M, P], etc.
# Captures the bracketed group; numbers are extracted in a second pass.
_CITATION_RE = re.compile(r"\[(\d+(?:\s*[,\-–]\s*\d+)*)\]")


def extract_citation_numbers(text: str) -> set[int]:
    """Extract all citation numbers from in-prose [N], [N,M], [N-M] patterns."""
    nums: set[int] = set()
    for m in _CITATION_RE.finditer(text):
        group = m.group(1)
        # Split by comma first
        for part in re.split(r"\s*,\s*", group):
            # Then check for hyphen ranges
            range_m = re.match(r"^(\d+)\s*[\-–]\s*(\d+)$", part)
            if range_m:
                lo, hi = int(range_m.group(1)), int(range_m.group(2))
                if lo <= hi and (hi - lo) <= 100:  # sanity bound
                    nums.update(range(lo, hi + 1))
            else:
                try:
                    nums.add(int(part.strip()))
                except ValueError:
                    pass
    return nums


# Numbered-reference line pattern in references.md.
# Matches "1. Smith J et al..." or "[1] Smith J et al..." at line start.
_NUMBERED_REF_RE = re.compile(
    # Match `[N]` or `N.` at line start, allowing optional leading emphasis
    # characters (`*`, `_`) and whitespace. The format-references-md output
    # uses `**[N] Author Year...**` (bold-prefix); plain numbered lists use
    # `[N] ...` or `N. ...`. All three forms count as a reference entry.
    r"^[*_\s]*(?:\[(\d+)\]|(\d+)\.)\s+", re.MULTILINE
)


def extract_reference_numbers(references_md: str) -> set[int]:
    """Extract numeric reference IDs from a references.md file."""
    nums: set[int] = set()
    for m in _NUMBERED_REF_RE.finditer(references_md):
        n = m.group(1) or m.group(2)
        try:
            nums.add(int(n))
        except (ValueError, TypeError):
            pass
    return nums


# BibTeX entry pattern: @article{Smith2023, ...} or @misc{key, ...}
_BIB_ENTRY_RE = re.compile(r"^\s*@\w+\s*\{\s*([^,]+?)\s*,", re.MULTILINE)


def extract_bib_keys(bibliography_bib: str) -> set[str]:
    """Extract entry keys from a bibliography.bib file."""
    return {m.group(1).strip() for m in _BIB_ENTRY_RE.finditer(bibliography_bib)}


# P-value pattern: matches "p < 0.05", "p=0.001", "p ≤ 0.001", "p-value 0.03", etc.
_PVALUE_RE = re.compile(
    r"\bp(?:\s*-?\s*value)?\s*(?:[<>=≤≥]+|\bof\b)\s*0?\.\d+",
    re.IGNORECASE,
)

# Multiple-testing correction methods we recognize.
_CORRECTION_METHODS = (
    "bonferroni",
    "benjamini[-\\s]?hochberg",
    "fdr",
    "false[-\\s]?discovery[-\\s]?rate",
    "holm",
    "holm[-\\s]?bonferroni",
    "holm[-\\s]?sidak",
    "sidak",
    "tukey",
    "scheffe",
    "dunn",
    "family[-\\s]?wise",
    "fwer",
    "q[-\\s]?value",
)
_CORRECTION_RE = re.compile(
    r"\b(?:" + "|".join(_CORRECTION_METHODS) + r")\b", re.IGNORECASE
)


# Statistical-test name patterns (used by M5 + M6 to count tests).
_TEST_NAMES = (
    "fisher.{0,5}exact",
    "chi[-\\s]?squared?",
    "mann[-\\s]?whitney",
    "wilcoxon",
    "t[-\\s]?test",
    "welch.{0,5}test",
    "anova",
    "kruskal[-\\s]?wallis",
    "spearman",
    "pearson",
    "kendall",
    "logistic\\s+regression",
    "linear\\s+regression",
    "cox\\s+regression",
    "kaplan[-\\s]?meier",
    "log[-\\s]?rank",
    "permutation\\s+test",
    "bootstrap",
    "g[-\\s]?test",
)
_TEST_RE = re.compile(
    r"\b(?:" + "|".join(_TEST_NAMES) + r")\b", re.IGNORECASE
)

# Software+version pattern for M5. Matches "scipy 1.11", "R 4.1.2", etc.
_SOFTWARE_VERSION_RE = re.compile(
    r"\b(?:scipy|numpy|scikit[-\s]?learn|sklearn|pandas|statsmodels|"
    r"matplotlib|seaborn|r\b|matlab|stata|python|sas|spss|jamovi|"
    r"prism|graphpad|networkx|biopython|nbformat)\s+v?\d+\.\d+(?:\.\d+)?",
    re.IGNORECASE,
)
# Also accept references to a requirements.txt or environment.yml file.
_REQUIREMENTS_REF_RE = re.compile(
    r"\b(?:requirements\.txt|environment\.yml|pyproject\.toml|pipfile)\b",
    re.IGNORECASE,
)
# Or an explicit "Software" subsection.
_SOFTWARE_HEADER_RE = re.compile(
    r"^\s*#{1,4}\s*software\b", re.IGNORECASE | re.MULTILINE
)


# Bare-percentage pattern: a percentage NOT preceded by a count construct
# like "42/156" or "42 of 156". This is M8.
# Strategy: find all "X.Y%" or "X%" tokens; for each, look at the preceding
# 60 chars for a count pattern. If absent, flag as bare.
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")

# Accept several count-precedes-pct constructions:
#   "42/156 (26.9%)"
#   "42 of 156 (26.9%)"
#   "42 out of 156 (26.9%)"
#   "Of 156 isolates, 42 (26.9%)" — count appears second; matches the
#       "<count> (<pct>%)" tail
#   "n = 156, 42 (26.9%)"
_COUNT_PRECEDES_PCT_RE = re.compile(
    r"(?:"
    r"\d+\s*(?:/|out of|of)\s*\d+\s*\(?\s*\d+(?:\.\d+)?\s*%"  # 42/156 (26.9%)
    r"|"
    r"\d+\s*\(\s*\d+(?:\.\d+)?\s*%"  # 42 (26.9% — closing paren may be outside window
    r")",
    re.IGNORECASE,
)


# AI-disclosure detection: must contain at least one AI tool reference AND
# action language indicating use.
_AI_TOOL_PATTERNS = (
    r"\bclaude\b",
    r"\bgpt\b",
    r"\bllm\b",
    r"\bai[-\s]assist",
    r"\bai[-\s]generated",
    r"\blarge\s+language\s+model",
    r"\bberil[-\s]paper[-\s]writer\b",
    r"\bberil[-\s]adversarial\b",
    r"\bbeR?il\b",  # BERIL capitalized variants
    r"chatbot",
)
_AI_ACTION_PATTERNS = (
    r"\bused\b",
    r"\bdraft(?:ed|ing)?\b",
    r"\bgenerated\b",
    r"\bassisted\b",
    r"\bemploy(?:ed|ing)?\b",
    r"\bauthored\b",
    r"\bwritten with\b",
    r"\bproduced by\b",
)
_AI_TOOL_RE = re.compile("|".join(_AI_TOOL_PATTERNS), re.IGNORECASE)
_AI_ACTION_RE = re.compile("|".join(_AI_ACTION_PATTERNS), re.IGNORECASE)


# Data-availability content check.
_URL_OR_ACCESSION_RE = re.compile(
    r"\bhttps?://\S+|"  # URL
    r"\b(?:doi|pmid|pmcid|arxiv|biorxiv|geo|sra|ena|ncbi|kbase)[:\s]?\s*\S+|"
    r"\b(?:gse\d+|prjna\d+|samn\d+|prjeb\d+)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def validate_M1_imrad_sections(
    sections: dict[str, str], mode: str
) -> ValidatorResult:
    """M1 — IMRAD sections present (or REPORT-mode equivalent)."""
    if mode == "report":
        required = REPORT_REQUIRED_SECTIONS
    else:
        required = PAPER_REQUIRED_SECTIONS

    missing: list[str] = []
    for canonical, aliases in required.items():
        if find_section(sections, aliases) is None:
            missing.append(canonical)

    if not missing:
        return ValidatorResult(id="M1", name="Required sections present", status="pass")

    violations = [
        Violation(
            severity="error",
            section="(global)",
            line=None,
            message=(
                f"Missing required {mode}-mode section: '{m}' "
                f"(accepted aliases: {required[m]})"
            ),
            escalation_path="auto-fix",
        )
        for m in missing
    ]
    return ValidatorResult(
        id="M1",
        name="Required sections present",
        status="fail",
        violations=violations,
    )


def validate_M2_structured_abstract(
    sections: dict[str, str], mode: str
) -> ValidatorResult:
    """M2 — Structured abstract (paper mode only)."""
    if mode == "report":
        return ValidatorResult(
            id="M2", name="Structured abstract", status="not-applicable",
        )
    abstract = find_section(sections, PAPER_REQUIRED_SECTIONS["abstract"])
    if abstract is None:
        return ValidatorResult(
            id="M2",
            name="Structured abstract",
            status="fail",
            violations=[Violation(
                severity="error",
                section="abstract",
                line=None,
                message="No Abstract section found; cannot check structure.",
                escalation_path="auto-fix",
            )],
        )

    # Look for subsection markers. They can be H3 headers (### Background)
    # or bold-prefix paragraphs (**Background:** ...).
    found = []
    missing = []
    abstract_lower = abstract.lower()
    for canonical, aliases in ABSTRACT_SUBSECTIONS.items():
        any_alias_present = False
        for a in aliases:
            # H3 header
            if re.search(r"^#{2,4}\s+" + re.escape(a) + r"\b",
                         abstract, re.IGNORECASE | re.MULTILINE):
                any_alias_present = True
                break
            # Bold prefix. Permissive: matches `**Background:**` (bold),
            # `**_Background:_**` (bold-italic, the form abstract.v1
            # actually emits — line 52 of the prompt), `**Background **`,
            # `***Background:***`, etc. Allows optional emphasis chars
            # (`_` / `*`) inside the outer `**` plus optional whitespace
            # and a trailing colon.
            if re.search(
                r"\*\*[_*]?\s*" + re.escape(a) + r"[:\s]?\s*[_*]?\*\*",
                abstract, re.IGNORECASE,
            ):
                any_alias_present = True
                break
        if any_alias_present:
            found.append(canonical)
        else:
            missing.append(canonical)

    if not missing:
        return ValidatorResult(
            id="M2", name="Structured abstract", status="pass",
        )
    return ValidatorResult(
        id="M2",
        name="Structured abstract",
        status="fail",
        violations=[Violation(
            severity="error",
            section="abstract",
            line=None,
            message=(
                f"Abstract is missing required subsection(s): "
                f"{', '.join(missing)}. Use H3 headers (### Background) "
                f"or bold prefixes (**Background:** ...)."
            ),
            escalation_path="auto-fix",
        )],
    )


def validate_M3_ai_disclosure(
    sections: dict[str, str], mode: str
) -> ValidatorResult:
    """M3 — AI-disclosure paragraph names tool + action."""
    # Look in Methods, Acknowledgments, or a dedicated 'AI' section.
    candidate_names = (
        "methods", "materials and methods", "acknowledgments",
        "acknowledgements", "ai disclosure", "ai-assisted analysis",
        "use of ai tools",
    )
    candidate_text = ""
    for name, text in sections.items():
        if name in candidate_names or "ai" in name or "acknowledg" in name:
            candidate_text += "\n" + text

    if not candidate_text.strip():
        # Also fall back to the full manuscript if no obvious section.
        candidate_text = "\n".join(sections.values())

    has_tool = bool(_AI_TOOL_RE.search(candidate_text))
    has_action = bool(_AI_ACTION_RE.search(candidate_text))

    if has_tool and has_action:
        return ValidatorResult(id="M3", name="AI disclosure", status="pass")

    msg_parts = []
    if not has_tool:
        msg_parts.append("no AI tool name found")
    if not has_action:
        msg_parts.append("no AI use-action verb found")
    return ValidatorResult(
        id="M3",
        name="AI disclosure",
        status="fail",
        violations=[Violation(
            severity="error",
            section="methods or acknowledgments",
            line=None,
            message=(
                f"AI-disclosure paragraph required by ICMJE V.A (Jan 2026): "
                f"{', '.join(msg_parts)}. Add a paragraph in Methods or "
                f"Acknowledgments naming the AI tool(s), version, and what "
                f"task they performed."
            ),
            escalation_path="auto-fix",
        )],
    )


def validate_M4_data_availability(
    sections: dict[str, str], mode: str
) -> ValidatorResult:
    """M4 — Data availability statement: present, non-trivial, contains URL/accession."""
    candidate_names = (
        "data availability", "data and code availability",
        "code and data availability", "availability",
    )
    # split_into_sections treats H1 and H2 as peer-level entries. When a
    # data-availability section is structured with H2 sub-sections (Code,
    # Data sources, Public accessions, etc. — the orchestrator's
    # template), the H1 entry has only the header line and the H2
    # sub-sections become separate dict entries. To compute body length
    # honestly we must also include the H2 sub-sections that follow the
    # H1 in document order, up to the next H1.
    text = ""
    matched_h1_idx = -1
    section_keys = list(sections.keys())  # insertion-ordered (from split_into_sections)
    for name in candidate_names:
        for idx, key in enumerate(section_keys):
            if section_matches_alias(key, (name,)):
                text = sections[key]
                matched_h1_idx = idx
                break
        if text:
            break

    # If matched, gather all subsequent sections until the next "top-level"
    # section (one whose first non-blank line begins with `# ` not `## `).
    # The current section's text (`text`) is the H1 body; sub-sections
    # contribute their content too.
    if matched_h1_idx >= 0:
        gathered: list[str] = [text]
        for nxt_key in section_keys[matched_h1_idx + 1:]:
            nxt_text = sections[nxt_key]
            # Find the first non-blank line; if it starts with `# ` (H1),
            # we've left the data-availability scope. If it starts with
            # `## ` (H2) or content directly, it's a sub-section/continuation.
            first_nonblank = next(
                (l for l in nxt_text.split("\n") if l.strip()), ""
            )
            if first_nonblank.startswith("# ") and not first_nonblank.startswith("## "):
                break
            gathered.append(nxt_text)
        text = "\n".join(gathered)

    if not text:
        # Fall back: search Methods or end of document for a "Data availability:" line.
        for sec_name, sec_text in sections.items():
            if "data availab" in sec_text.lower():
                text = sec_text
                break

    if not text:
        return ValidatorResult(
            id="M4",
            name="Data availability statement",
            status="fail",
            violations=[Violation(
                severity="error",
                section="(global)",
                line=None,
                message=(
                    "No 'Data Availability' section or statement found. ICMJE "
                    "requires a data-availability statement; 'available upon "
                    "request' is not acceptable."
                ),
                escalation_path="auto-fix",
            )],
        )

    body = "\n".join(l for l in text.split("\n") if not l.startswith("#"))
    if len(body.strip()) < 100:
        return ValidatorResult(
            id="M4",
            name="Data availability statement",
            status="fail",
            violations=[Violation(
                severity="error",
                section="data availability",
                line=None,
                message=(
                    f"Data availability statement is too short ({len(body.strip())} "
                    f"chars; need >100). Specify repository URL, accession "
                    f"number(s), or explicit restriction rationale."
                ),
                escalation_path="user-modify",
            )],
        )

    if not _URL_OR_ACCESSION_RE.search(body):
        return ValidatorResult(
            id="M4",
            name="Data availability statement",
            status="fail",
            violations=[Violation(
                severity="error",
                section="data availability",
                line=None,
                message=(
                    "Data availability statement contains no URL or accession "
                    "number. 'Available upon request' is not acceptable per ICMJE; "
                    "name the repository, accession, or DOI."
                ),
                escalation_path="user-modify",
            )],
        )

    return ValidatorResult(
        id="M4", name="Data availability statement", status="pass",
    )


def validate_M5_software_versions(
    sections: dict[str, str], mode: str
) -> ValidatorResult:
    """M5 — Software + version mentions (soft-warning per §7.1.2)."""
    methods = find_section(sections, PAPER_REQUIRED_SECTIONS["methods"])
    if methods is None and mode == "report":
        methods = find_section(sections, REPORT_REQUIRED_SECTIONS["what_was_done"])
    if methods is None:
        return ValidatorResult(
            id="M5", name="Software + version", status="not-applicable",
        )

    word_count = section_word_count(methods)
    if word_count < 200:
        # Methods too short to require software statement; skip.
        return ValidatorResult(
            id="M5", name="Software + version", status="not-applicable",
        )

    test_count = len(_TEST_RE.findall(methods))
    if test_count < 3:
        # Few tests; no expectation.
        return ValidatorResult(
            id="M5", name="Software + version", status="not-applicable",
        )

    has_version = bool(_SOFTWARE_VERSION_RE.search(methods))
    has_requirements_ref = bool(_REQUIREMENTS_REF_RE.search(methods))
    has_software_section = bool(_SOFTWARE_HEADER_RE.search(methods))

    if has_version or has_requirements_ref or has_software_section:
        return ValidatorResult(
            id="M5", name="Software + version", status="pass",
        )

    return ValidatorResult(
        id="M5",
        name="Software + version",
        status="soft-warning",
        violations=[Violation(
            severity="warning",
            section="methods",
            line=None,
            message=(
                f"M5 (soft): Methods names {test_count} statistical test(s) "
                f"in {word_count} words but does not appear to specify "
                f"software versions (e.g., 'scipy 1.11', 'R 4.1.2'). "
                f"ICMJE IV.A.3.d / SAMPL §1 recommend versioned tool "
                f"statements. Acceptable alternatives: an explicit "
                f"requirements.txt / environment.yml reference, or a "
                f"'Software' subsection."
            ),
            escalation_path="user-modify",
        )],
    )


def validate_M6_multiple_testing(
    sections: dict[str, str], mode: str
) -> ValidatorResult:
    """M6 — Multiple-testing correction declared if ≥5 distinct tests reported."""
    results = find_section(sections, PAPER_REQUIRED_SECTIONS["results"])
    if results is None and mode == "report":
        results = find_section(
            sections, REPORT_REQUIRED_SECTIONS["what_was_observed"]
        )
    if results is None:
        return ValidatorResult(
            id="M6", name="Multiple-testing correction", status="not-applicable",
        )

    pvalue_count = len(_PVALUE_RE.findall(results))
    if pvalue_count < 5:
        return ValidatorResult(
            id="M6", name="Multiple-testing correction", status="pass",
        )

    # Look for a correction-method reference anywhere in the manuscript
    # (often appears in Methods, not Results).
    full_text = "\n".join(sections.values())
    if _CORRECTION_RE.search(full_text):
        return ValidatorResult(
            id="M6", name="Multiple-testing correction", status="pass",
        )

    return ValidatorResult(
        id="M6",
        name="Multiple-testing correction",
        status="fail",
        violations=[Violation(
            severity="error",
            section="results",
            line=None,
            message=(
                f"Results reports {pvalue_count} p-values but Methods/Results "
                f"do not declare a multiple-testing correction method "
                f"(Bonferroni, Benjamini-Hochberg / FDR, Holm, etc.). "
                f"At α=0.05 uncorrected, ~{round(pvalue_count * 0.05, 1)} of "
                f"these are expected to reach significance by chance."
            ),
            escalation_path="escalate",
        )],
    )


def validate_M7_effect_sizes(
    sections: dict[str, str], mode: str
) -> ValidatorResult:
    """M7 — Numerical claims have effect size + CI, not bare percentages.

    v0.1 implementation is a soft warning: it flags Results-section claims
    of statistical significance (p-value present) that lack a confidence-
    interval marker within the same paragraph. Bare-percentage detection is
    handled by M8 to avoid double-flagging.
    """
    results = find_section(sections, PAPER_REQUIRED_SECTIONS["results"])
    if results is None and mode == "report":
        results = find_section(
            sections, REPORT_REQUIRED_SECTIONS["what_was_observed"]
        )
    if results is None:
        return ValidatorResult(
            id="M7", name="Effect sizes + CIs", status="not-applicable",
        )

    paragraphs = re.split(r"\n\s*\n", results)
    flagged: list[Violation] = []
    ci_re = re.compile(
        r"\b(?:95\s*%?\s*ci|confidence\s+interval|credible\s+interval|"
        r"\[\s*-?\d+\.?\d*\s*[,–\-]\s*-?\d+\.?\d*\s*\])",
        re.IGNORECASE,
    )
    effect_size_re = re.compile(
        r"\b(?:odds\s+ratio|hazard\s+ratio|risk\s+ratio|or\s*=|hr\s*=|rr\s*=|"
        r"cohen.{0,5}d|effect\s+size|mean\s+difference|spearman|pearson|"
        r"correlation|coefficient|β\s*=|beta\s*=)",
        re.IGNORECASE,
    )

    for i, para in enumerate(paragraphs, start=1):
        if not _PVALUE_RE.search(para):
            continue
        has_ci = bool(ci_re.search(para))
        has_effect = bool(effect_size_re.search(para))
        if has_ci and has_effect:
            continue
        missing = []
        if not has_effect:
            missing.append("effect size")
        if not has_ci:
            missing.append("95% CI or credible interval")
        flagged.append(Violation(
            severity="warning",
            section="results",
            line=None,
            message=(
                f"Paragraph {i} reports a p-value without {' and '.join(missing)}. "
                f"SAMPL §2 requires effect-size + CI accompanying p-values. "
                f"This is a soft warning; if the paragraph is descriptive "
                f"rather than inferential, accept-as-limitation is appropriate."
            ),
            escalation_path="user-modify",
        ))

    if not flagged:
        return ValidatorResult(id="M7", name="Effect sizes + CIs", status="pass")
    return ValidatorResult(
        id="M7",
        name="Effect sizes + CIs",
        status="soft-warning",
        violations=flagged,
    )


def validate_M8_counts_before_percentages(
    sections: dict[str, str], mode: str
) -> ValidatorResult:
    """M8 — Counts (n) precede derivatives (%): "42/156 (26.9%)" not "26.9%"."""
    results = find_section(sections, PAPER_REQUIRED_SECTIONS["results"])
    if results is None and mode == "report":
        results = find_section(
            sections, REPORT_REQUIRED_SECTIONS["what_was_observed"]
        )
    if results is None:
        return ValidatorResult(
            id="M8",
            name="Counts before percentages",
            status="not-applicable",
        )

    flagged: list[Violation] = []
    for m in _PCT_RE.finditer(results):
        start, end = m.span()
        # Look back 60 chars for a count-before-pct construct.
        # Look forward 20 chars to catch closing parens and trailing "CI".
        window_start = max(0, start - 60)
        window_end = min(len(results), end + 20)
        window = results[window_start:window_end]
        if _COUNT_PRECEDES_PCT_RE.search(window):
            continue
        # Also accept if the percentage is preceded by N=XX or n=XX in the
        # same sentence.
        sentence_window = results[max(0, start - 200):end]
        if re.search(r"\bn\s*=\s*\d+", sentence_window, re.IGNORECASE):
            continue
        # Skip if it's clearly a confidence level ("95% CI") or significance
        # threshold ("5%") rather than a result. Look ahead for "CI",
        # "confidence", "credible".
        forward_ctx = results[end:min(len(results), end + 25)]
        if re.search(
            r"^\s*(?:ci|confidence|credible)\b",
            forward_ctx, re.IGNORECASE,
        ):
            continue
        # Also skip the bare "95%" / "99%" / "90%" / "5%" / "1%" confidence
        # markers that appear without the CI keyword (rare but possible).
        if re.match(r"^(?:95|99|90)$", m.group(1)):
            continue
        # Locate line number
        line_no = results[:start].count("\n") + 1
        flagged.append(Violation(
            severity="warning",
            section="results",
            line=line_no,
            message=(
                f"Bare percentage '{m.group()}' on line {line_no} of Results: "
                f"add the underlying count, e.g., '42/156 (26.9%)'. "
                f"ICMJE IV.A.3.e."
            ),
            escalation_path="user-modify",
        ))

    if not flagged:
        return ValidatorResult(
            id="M8", name="Counts before percentages", status="pass",
        )
    # Bare percentages are a soft-warning in v0.1 (high false-positive risk).
    return ValidatorResult(
        id="M8",
        name="Counts before percentages",
        status="soft-warning",
        violations=flagged,
    )


def validate_M9_limitations(
    sections: dict[str, str], mode: str
) -> ValidatorResult:
    """M9 — Limitations section present with substantive content (>150 chars)."""
    if mode == "report":
        candidate = find_section(sections, REPORT_REQUIRED_SECTIONS["limitations"])
    else:
        candidate = find_section(sections, ("limitations", "limitations and caveats"))

    if candidate is None:
        return ValidatorResult(
            id="M9",
            name="Limitations section",
            status="fail",
            violations=[Violation(
                severity="error",
                section="(global)",
                line=None,
                message=(
                    "No Limitations section found. ICMJE IV.A.3.f requires "
                    "Discussion to address limitations explicitly."
                ),
                escalation_path="auto-fix",
            )],
        )

    body = "\n".join(l for l in candidate.split("\n") if not l.startswith("#"))
    if len(body.strip()) < 150:
        return ValidatorResult(
            id="M9",
            name="Limitations section",
            status="fail",
            violations=[Violation(
                severity="error",
                section="limitations",
                line=None,
                message=(
                    f"Limitations section is too short ({len(body.strip())} "
                    f"chars; need >150). Substantive limitations include "
                    f"sample-size constraints, generalizability scope, "
                    f"computational method caveats."
                ),
                escalation_path="user-modify",
            )],
        )
    return ValidatorResult(id="M9", name="Limitations section", status="pass")


def validate_M10_citations_crossref(
    sections: dict[str, str],
    mode: str,
    references_md: Optional[str] = None,
    bibliography_bib: Optional[str] = None,
) -> ValidatorResult:
    """M10 — Every citation in prose appears in references.md AND bibliography.bib."""
    full_prose = "\n".join(sections.values())
    cited_nums = extract_citation_numbers(full_prose)

    # If no citations at all, vacuously pass (warn separately if the manuscript
    # is long but unreferenced — out of M10 scope).
    if not cited_nums:
        return ValidatorResult(
            id="M10", name="Citation cross-reference", status="pass",
        )

    violations: list[Violation] = []

    if references_md is None:
        violations.append(Violation(
            severity="error",
            section="(global)",
            line=None,
            message=(
                f"Prose contains {len(cited_nums)} numbered citation(s) "
                f"but no references.md file was provided. Citations must be "
                f"cross-referenceable to a numbered reference list."
            ),
            escalation_path="auto-fix",
        ))
    else:
        ref_nums = extract_reference_numbers(references_md)
        missing_in_refs = sorted(cited_nums - ref_nums)
        if missing_in_refs:
            violations.append(Violation(
                severity="error",
                section="references",
                line=None,
                message=(
                    f"Citation number(s) {missing_in_refs} appear in prose but "
                    f"are missing from references.md. Either fix the citation "
                    f"numbering or add the missing reference(s)."
                ),
                escalation_path="auto-fix",
            ))

    if bibliography_bib is not None:
        bib_keys = extract_bib_keys(bibliography_bib)
        if references_md is not None:
            ref_count = len(extract_reference_numbers(references_md))
            # Bibliography may legitimately have MORE entries than references
            # (uncited pool entries survive in bib for audit; we don't strip
            # them at finalize time). Flag only the converse: bibliography
            # missing entries that are cited in references.md.
            if ref_count > 0 and len(bib_keys) < ref_count:
                violations.append(Violation(
                    severity="warning",
                    section="bibliography",
                    line=None,
                    message=(
                        f"references.md has {ref_count} numbered entries but "
                        f"bibliography.bib has only {len(bib_keys)} entries. "
                        f"Some cited references appear to be missing from "
                        f"the BibTeX file."
                    ),
                    escalation_path="user-modify",
                ))

    if not violations:
        return ValidatorResult(
            id="M10", name="Citation cross-reference", status="pass",
        )
    # Hard error if any "error" severity present, soft otherwise.
    if any(v.severity == "error" for v in violations):
        status = "fail"
    else:
        status = "soft-warning"
    return ValidatorResult(
        id="M10",
        name="Citation cross-reference",
        status=status,
        violations=violations,
    )


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def run_all_validators(
    draft_dir: Path, mode: str = "paper"
) -> ValidationReport:
    """Run every validator against a draft directory, return a full report."""
    sections = load_manuscript_sections(draft_dir)

    references_md_path = draft_dir / "references.md"
    references_md = (
        references_md_path.read_text(encoding="utf-8")
        if references_md_path.is_file()
        else None
    )
    bibliography_bib_path = draft_dir / "bibliography.bib"
    bibliography_bib = (
        bibliography_bib_path.read_text(encoding="utf-8")
        if bibliography_bib_path.is_file()
        else None
    )

    results = [
        validate_M1_imrad_sections(sections, mode),
        validate_M2_structured_abstract(sections, mode),
        validate_M3_ai_disclosure(sections, mode),
        validate_M4_data_availability(sections, mode),
        validate_M5_software_versions(sections, mode),
        validate_M6_multiple_testing(sections, mode),
        validate_M7_effect_sizes(sections, mode),
        validate_M8_counts_before_percentages(sections, mode),
        validate_M9_limitations(sections, mode),
        validate_M10_citations_crossref(
            sections, mode,
            references_md=references_md,
            bibliography_bib=bibliography_bib,
        ),
    ]

    return ValidationReport(
        draft_dir=str(draft_dir),
        mode=mode,
        validators=results,
    )


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="validate_manuscript.py",
        description=(
            "Run M1-M10 mechanized validators on a paper draft directory. "
            "Outputs a JSON ValidationReport to stdout. Exit code 0 on "
            "all-pass or all-not-applicable; 1 on any hard fail; 0 with "
            "soft-warnings present (caller decides escalation)."
        ),
    )
    p.add_argument(
        "draft_dir",
        type=Path,
        help="Path to the paper draft directory (papers/draft_N/).",
    )
    p.add_argument(
        "--mode",
        choices=("paper", "report"),
        default="paper",
        help="Output mode (default: paper). Affects which sections are required.",
    )
    p.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Optional path to write JSON output (default: stdout).",
    )
    args = p.parse_args(argv)

    if not args.draft_dir.is_dir():
        print(
            f"Error: draft_dir does not exist or is not a directory: "
            f"{args.draft_dir}",
            file=sys.stderr,
        )
        return 2

    report = run_all_validators(args.draft_dir, mode=args.mode)
    payload = json.dumps(report.to_dict(), indent=2)

    if args.output is not None:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        sys.stdout.write(payload + "\n")

    summary = report.to_dict()["summary"]
    if summary["overall_status"] == "fail":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
