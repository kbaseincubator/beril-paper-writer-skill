#!/usr/bin/env python3
"""check_caption_provenance.py — Source 4 caption fabrication detector.

Standalone script invoked by the shell orchestrator after
phase_caption_synthesis runs:

    python3 "$SKILL_DIR/tools/check_caption_provenance.py" "$DRAFT_DIR"

Sixth post-processor in the v0.4 pattern (joins
`check_throughline_glyphs`, `check_scope_coherence`, `check_overclaim`,
`check_repair_scope`, `check_figures_manifest`). Validates that
LLM-synthesized figure captions (Source 4 / `figure_caption.v1.md`)
don't fabricate numerical claims, named entities, or panel letters that
have no trace in the input bundle the orchestrator passed to the prompt.

Why this exists. Source 4 is the LLM-synthesis fallback when Source 2
(notebook walk-back) and Source 3 (matplotlib AST) yield insufficient
signal for a 30-word ICMJE-style legend. The prompt
(`prompts/figure_caption.v1.md`) instructs the LLM to self-enforce
anti-fabrication discipline, but per
`feedback_prompt_discipline_needs_post_check.md`, prompt-level
enforcement alone is unreliable. This script is the second layer.

Four checks:
  1. **Numerical-claim trace.** Every digit / percent / threshold in
     the caption must appear in one of the prompt's input fields
     (descriptor.notebook_prose, descriptor.axes_labels,
     descriptor.panels[*] text, prose_panel_callouts values,
     report_prose, results_section_prose). WARN per ungrounded token.
  2. **Named-entity trace.** Multi-word capitalized phrases in the
     caption must appear in input fields. WARN per ungrounded entity.
  3. **Panel-letter hallucination.** Each `(A)` / `(B)` / etc. mention
     must trace to either `descriptor.panels[*].letter` or
     `prose_panel_callouts` keys. WARN per ungrounded letter.
  4. **Word-count compliance.** Caption word count must be 30-200
     (per Tier 8 AC). WARN if outside this range.

Behavior:
  - Emits stderr WARN / NOTE lines per anomaly + a final summary count.
  - Always exits 0. Advisory; orchestrator surfaces via emit-next-actions.

Inputs:
  - `<draft_dir>/audit/figure_caption.v1.metadata.json` — per-figure
    input bundles + output paths. Schema:
      {
        "schema_version": 1,
        "captions": [
          {
            "figure_id": 3,
            "output_path": "audit/figure_caption_3.md",  # rel to draft_dir
            "input_bundle": {
              "short_caption": "...",
              "structured_descriptor": {...},
              "prose_panel_callouts": {"A": "...", ...},
              "report_prose": "...",
              "results_section_prose": "...",
              "max_words": 200
            },
            "closing_message": {
              "word_count": 102,
              "traceable_claims": 8,
              "panel_count": 2
            },
            "source_chosen": "llm" | "deterministic"
          },
          ...
        ]
      }
  - For each entry with `source_chosen == "llm"`, read the caption file
    at `<draft_dir>/<output_path>` and run the four checks against the
    `input_bundle`.

The script can be imported as a module for unit testing; check helpers
are pure (caption text + bundle in, list of warnings out).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants — regexes tuned for scientific-prose tokens
# ---------------------------------------------------------------------------

# Matches numerical tokens including comma-separated thousands and
# common scientific suffixes (%, x, ×, ±, ~). Examples that match:
#   "12", "12.5", "12%", "1,000", "2.6×", "100±5".
# Examples that don't match:
#   "p < 0.05" (matches 0.05, leaves 'p <' alone — that's fine).
_NUMBER_TOKEN_RE = re.compile(
    r"\b(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?:%|x|×|±|~)?\b"
)

# Multi-word capitalized phrases (named entities).
# Two+ consecutive capitalized words separated by spaces.
_NAMED_ENTITY_RE = re.compile(
    r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b"
)

# Panel-letter mentions in caption. Forms:
#   "(A)" — explicit panel marker
#   "(panel A)" — verbose form
#   "panel A" — bare prose form
#   "panel labeled A"
# Captures the letter group.
_PANEL_MENTION_RE = re.compile(
    r"\((?:panel\s+)?([A-Z])\)|"
    r"\bpanel\s+(?:labeled\s+)?([A-Z])\b",
)

# Word count: split on whitespace, count non-empty tokens. Strips
# leading/trailing italic markers (* or _).
_WORD_TOKEN_RE = re.compile(r"\S+")

# Acceptable word-count band (per Tier 8 AC).
WORD_COUNT_MIN = 30
WORD_COUNT_MAX = 200


# ---------------------------------------------------------------------------
# Pure check helpers
# ---------------------------------------------------------------------------


def _flatten_bundle_text(input_bundle: dict) -> str:
    """Concatenate all text fields from the prompt's input bundle into
    one searchable corpus.

    Used by numerical-claim and named-entity grounding checks. Includes:
      - structured_descriptor.title / axes_labels / legend_labels /
        panels[*] (title/xlabel/ylabel/prose_context) / notebook_prose
      - prose_panel_callouts values
      - report_prose
      - results_section_prose
      - short_caption (the orchestrator passes this; legitimate source).
    """
    parts: list[str] = []
    desc = input_bundle.get("structured_descriptor") or {}
    if isinstance(desc, dict):
        if desc.get("title"):
            parts.append(str(desc["title"]))
        for label in desc.get("axes_labels") or []:
            parts.append(str(label))
        for label in desc.get("legend_labels") or []:
            parts.append(str(label))
        for panel in desc.get("panels") or []:
            if isinstance(panel, dict):
                for k in ("title", "xlabel", "ylabel", "prose_context"):
                    v = panel.get(k)
                    if v:
                        parts.append(str(v))
        if desc.get("notebook_prose"):
            parts.append(str(desc["notebook_prose"]))
    callouts = input_bundle.get("prose_panel_callouts") or {}
    if isinstance(callouts, dict):
        for v in callouts.values():
            if v:
                parts.append(str(v))
    if input_bundle.get("report_prose"):
        parts.append(str(input_bundle["report_prose"]))
    if input_bundle.get("results_section_prose"):
        parts.append(str(input_bundle["results_section_prose"]))
    if input_bundle.get("short_caption"):
        parts.append(str(input_bundle["short_caption"]))
    return "\n\n".join(parts)


def _normalize_number(token: str) -> str:
    """Normalize a number token for grounding lookup: strip commas,
    drop unit suffixes. '1,000' → '1000'; '2.6%' → '2.6'; '100±' → '100'."""
    t = token.replace(",", "")
    t = re.sub(r"[%xX×±~]+$", "", t)
    return t


def check_numerical_claims(caption: str, corpus: str) -> list[str]:
    """For each number in the caption, verify it appears in the corpus.

    Match either verbatim (e.g. caption '95%' against corpus '95%') OR
    with comma stripped (e.g. caption '3,705' against corpus '3705'
    or '3,705'). Returns a list of WARN strings, one per ungrounded token.
    """
    warnings: list[str] = []
    corpus_normalized = corpus
    corpus_no_commas = corpus.replace(",", "")
    seen_in_caption: set[str] = set()
    for m in _NUMBER_TOKEN_RE.finditer(caption):
        token = m.group(0)
        if token in seen_in_caption:
            continue
        seen_in_caption.add(token)
        normalized = _normalize_number(token)
        if (
            token in corpus_normalized
            or normalized in corpus_no_commas
            or normalized in corpus_normalized
        ):
            continue
        warnings.append(
            f"ungrounded numerical claim: {token!r} appears in caption "
            f"but not in input bundle"
        )
    return warnings


# Common-word allow-list for named-entity grounding. These are
# capitalized-multi-word phrases that legitimately come from English
# prose conventions and aren't fabrication signals.
_NAMED_ENTITY_ALLOW = {
    "Each Panel",
    "The Distribution",
    "Same Scale",
    "Note That",
    "Both Panels",
    "Panel A",
    "Panel B",
    "Panel C",
    "Panel D",
}


def check_named_entities(caption: str, corpus: str) -> list[str]:
    """Each capitalized multi-word phrase in the caption must appear
    in the corpus (case-sensitive substring match)."""
    warnings: list[str] = []
    seen: set[str] = set()
    for m in _NAMED_ENTITY_RE.finditer(caption):
        entity = m.group(0)
        if entity in seen:
            continue
        seen.add(entity)
        if entity in _NAMED_ENTITY_ALLOW:
            continue
        if entity in corpus:
            continue
        warnings.append(
            f"ungrounded named entity: {entity!r} appears in caption but "
            f"not in input bundle"
        )
    return warnings


def check_panel_letters(caption: str, input_bundle: dict) -> list[str]:
    """Each panel-letter mention in caption must trace to either
    descriptor.panels[*].letter OR prose_panel_callouts keys."""
    warnings: list[str] = []
    desc = input_bundle.get("structured_descriptor") or {}
    panels = desc.get("panels") or []
    callouts = input_bundle.get("prose_panel_callouts") or {}
    valid_letters: set[str] = set()
    for p in panels:
        if isinstance(p, dict) and isinstance(p.get("letter"), str):
            valid_letters.add(p["letter"].upper())
    for k in callouts.keys():
        if isinstance(k, str) and len(k) == 1 and k.isupper():
            valid_letters.add(k)

    seen: set[str] = set()
    for m in _PANEL_MENTION_RE.finditer(caption):
        letter = (m.group(1) or m.group(2) or "").upper()
        if not letter or letter in seen:
            continue
        seen.add(letter)
        if letter not in valid_letters:
            warnings.append(
                f"ungrounded panel letter: ({letter}) mentioned in caption "
                f"but not in descriptor.panels or prose_panel_callouts "
                f"(valid: {sorted(valid_letters) or 'none'})"
            )
    return warnings


def check_word_count(caption: str) -> list[str]:
    """Caption word count must be in [WORD_COUNT_MIN, WORD_COUNT_MAX]."""
    warnings: list[str] = []
    n = sum(1 for _ in _WORD_TOKEN_RE.finditer(caption))
    if n < WORD_COUNT_MIN:
        warnings.append(
            f"caption is {n} words; below minimum of {WORD_COUNT_MIN} "
            f"(Tier 8 AC). May indicate insufficient input signal — "
            f"orchestrator should consider falling back to deterministic "
            f"description."
        )
    elif n > WORD_COUNT_MAX:
        warnings.append(
            f"caption is {n} words; above maximum of {WORD_COUNT_MAX} "
            f"(Tier 8 AC). Prompt's word-count discipline failed."
        )
    return warnings


def check_caption(caption_text: str, input_bundle: dict) -> list[str]:
    """Run all four checks on one caption + input bundle. Returns
    consolidated WARN list. Empty list = no anomalies."""
    corpus = _flatten_bundle_text(input_bundle)
    out: list[str] = []
    out.extend(check_numerical_claims(caption_text, corpus))
    out.extend(check_named_entities(caption_text, corpus))
    out.extend(check_panel_letters(caption_text, input_bundle))
    out.extend(check_word_count(caption_text))
    return out


# ---------------------------------------------------------------------------
# Metadata-file orchestration
# ---------------------------------------------------------------------------


def load_metadata(metadata_path: Path) -> tuple[dict, list[str]]:
    """Load and validate the figure_caption.v1.metadata.json file."""
    warnings: list[str] = []
    if not metadata_path.is_file():
        return {}, [f"metadata file not found at {metadata_path}"]
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {}, [f"metadata file is invalid JSON: {e}"]
    if not isinstance(data, dict):
        return {}, [f"metadata root is not a JSON object"]
    if "captions" not in data:
        return data, [f"metadata has no 'captions' key"]
    if not isinstance(data["captions"], list):
        return data, [f"metadata.captions is not a list"]
    return data, warnings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_caption_provenance.py",
        description=(
            "Validate Source-4-synthesized figure captions against their "
            "input bundles. Advisory; always exits 0."
        ),
    )
    parser.add_argument(
        "draft_dir",
        help="Path to the paper draft directory (e.g., papers/draft_1/).",
    )
    args = parser.parse_args(argv)

    draft_dir = Path(args.draft_dir).expanduser().resolve()
    if not draft_dir.is_dir():
        sys.stderr.write(
            f"[check_caption_provenance] WARN: draft_dir does not exist: "
            f"{draft_dir}; skipping checks\n"
        )
        return 0

    metadata_path = draft_dir / "audit" / "figure_caption.v1.metadata.json"
    data, meta_warnings = load_metadata(metadata_path)

    all_lines: list[str] = []

    if not metadata_path.is_file():
        # No metadata — phase_caption_synthesis didn't run. NOTE-only;
        # not a WARN (deterministic-only drafts are valid).
        all_lines.append(
            "[check_caption_provenance] NOTE: figure_caption.v1.metadata.json "
            "not found — phase_caption_synthesis did not run, or no figures "
            "needed Source 4. Skipping caption-provenance checks."
        )
        for w in all_lines:
            sys.stderr.write(f"{w}\n")
        sys.stderr.write(
            "[check_caption_provenance] summary: 0 captions checked, "
            "0 WARN, 1 NOTE\n"
        )
        return 0

    for w in meta_warnings:
        all_lines.append(f"[check_caption_provenance] WARN: metadata: {w}")

    captions = data.get("captions", []) if isinstance(data, dict) else []
    n_checked = 0
    for entry in captions:
        if not isinstance(entry, dict):
            all_lines.append(
                "[check_caption_provenance] WARN: metadata: entry is not "
                "a JSON object; skipping"
            )
            continue
        if entry.get("source_chosen") != "llm":
            # Deterministic-source captions don't need provenance check;
            # the descriptor IS the source.
            continue
        figure_id = entry.get("figure_id")
        output_path = entry.get("output_path")
        if output_path is None:
            all_lines.append(
                f"[check_caption_provenance] WARN: figure_id={figure_id}: "
                f"output_path missing in metadata"
            )
            continue
        cap_path = draft_dir / output_path
        if not cap_path.is_file():
            all_lines.append(
                f"[check_caption_provenance] WARN: figure_id={figure_id}: "
                f"caption file not found at {cap_path}"
            )
            continue
        caption_text = cap_path.read_text(encoding="utf-8").strip()
        bundle = entry.get("input_bundle") or {}
        per_caption_warnings = check_caption(caption_text, bundle)
        for w in per_caption_warnings:
            all_lines.append(
                f"[check_caption_provenance] WARN: figure_id={figure_id}: {w}"
            )
        n_checked += 1

    # Emit.
    for w in all_lines:
        sys.stderr.write(f"{w}\n")

    n_warn = sum(1 for w in all_lines if "WARN" in w[:50])
    n_note = sum(1 for w in all_lines if "NOTE" in w[:50])
    sys.stderr.write(
        f"[check_caption_provenance] summary: {n_checked} captions checked, "
        f"{n_warn} WARN, {n_note} NOTE\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
