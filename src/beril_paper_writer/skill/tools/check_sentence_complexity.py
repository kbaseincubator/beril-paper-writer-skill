#!/usr/bin/env python3
"""check_sentence_complexity.py — Sentence complexity advisory checker.

Standalone script invoked by the shell orchestrator after paper assembly:

    python3 "$SKILL_DIR/tools/check_sentence_complexity.py" "$DRAFT_DIR"

Scans all section markdown files (02_results.md, 03_discussion.md,
04_introduction.md, 05_abstract.md) for sentence-level complexity issues:

  1. **WARN if >50 words:** Likely too long; readability concern.
  2. **WARN if 2+ parenthetical pairs:** Nested parentheses reduce clarity.
  3. **NOTE if >40 words:** Softer threshold for awareness.

Skips code blocks (``` fenced), YAML frontmatter (--- blocks), and
markdown image tags ![...](...)

Sentence splitting heuristic:
  - Split on `. ` followed by uppercase, `.\n`, `? `, `! `, or para-end.
  - Preserve decimals (1.34), abbreviations (et al., Fig., e.g., etc.),
    and parenthetical content.

Emits stderr WARN/NOTE lines per issue + JSON diagnostics to stdout.
**Always exits 0.** Advisory; same contract as other post-checkers.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Sentence extraction and analysis
# ---------------------------------------------------------------------------

def _is_abbreviation(word: str) -> bool:
    """Check if word is a known abbreviation that shouldn't split."""
    abbrevs = {
        "et al", "e.g", "i.e", "vs", "fig", "figs", "dr", "mr", "mrs",
        "prof", "sr", "jr", "inc", "ltd", "ph d", "no", "eq",
    }
    return word.lower() in abbrevs


def _split_into_sentences(text: str) -> list[str]:
    """Split text into sentences, preserving decimals and abbreviations.

    Handles:
      - `. ` followed by uppercase letter
      - `?\n`, `!\n`, `.\n`
      - `? ` and `! `
      - End of paragraph
    """
    # Remove lines that are code blocks or frontmatter.
    lines = text.split("\n")
    filtered_lines = []
    in_code_block = False
    in_frontmatter = False

    for line in lines:
        stripped = line.strip()

        # YAML frontmatter
        if stripped == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue

        # Code blocks
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        # Skip markdown image tags
        if re.match(r"^!\[.*?\]\(.*?\)$", stripped):
            continue

        filtered_lines.append(line)

    text = "\n".join(filtered_lines)

    # Replace newlines with spaces (preserve para breaks).
    text = re.sub(r"\n\n+", "\n___PARA_BREAK___\n", text)
    text = re.sub(r"\n", " ", text)
    text = text.replace("___PARA_BREAK___", "\n")

    sentences = []
    current = ""

    i = 0
    while i < len(text):
        char = text[i]
        current += char

        # Check for sentence-ending punctuation.
        if char in ".?!":
            # Lookahead for next character.
            if i + 1 < len(text):
                next_char = text[i + 1]

                # Period: check if it's an abbreviation or decimal.
                if char == ".":
                    # Decimal check: digit before and after.
                    if (
                        i > 0
                        and text[i - 1].isdigit()
                        and i + 1 < len(text)
                        and text[i + 1].isdigit()
                    ):
                        # It's a decimal point; skip.
                        i += 1
                        continue

                    # Abbreviation check: extract word before period.
                    # Look backwards for start of word.
                    j = i - 1
                    while j >= 0 and (text[j].isalnum() or text[j] == " "):
                        j -= 1
                    word = text[j + 1 : i].strip()

                    # If it looks like an abbreviation, keep going.
                    if _is_abbreviation(word):
                        i += 1
                        continue

                    # If not followed by space or newline, keep going.
                    if next_char not in " \n":
                        i += 1
                        continue

                    # If followed by space and lowercase, keep going.
                    if next_char == " " and i + 2 < len(text):
                        after_space = text[i + 2]
                        if after_space.islower():
                            i += 1
                            continue

                # Question or exclamation: end sentence on any following
                # space, newline, or end of text.
                elif char in "?!":
                    if next_char not in " \n":
                        i += 1
                        continue

            # End of sentence.
            sentence = current.strip()
            if sentence:
                sentences.append(sentence)
            current = ""
        elif char == "\n":
            # Paragraph break.
            sentence = current.rstrip()
            if sentence and sentence not in (" ", "\n"):
                sentences.append(sentence)
            current = ""

        i += 1

    # Remaining text.
    sentence = current.strip()
    if sentence:
        sentences.append(sentence)

    return [s for s in sentences if s]


def _count_words(sentence: str) -> int:
    """Count words in a sentence (split on whitespace)."""
    return len(sentence.split())


def _count_parenthetical_pairs(sentence: str) -> int:
    """Count parenthetical pairs in a sentence.

    E.g., "(foo)" and "(bar)" = 2 pairs.
    Nested pairs like "(foo (bar))" count as 2.
    """
    count = sentence.count("(")
    return count


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _check_sentence_length(sentences: list[str]) -> list[tuple[str, int, str]]:
    """Check for sentences >50 words (WARN) and >40 words (NOTE).

    Returns list of (level, word_count, first_60_chars) tuples.
    """
    findings = []
    for sentence in sentences:
        word_count = _count_words(sentence)
        first_60 = (sentence[:60] + "...") if len(sentence) > 60 else sentence
        if word_count > 50:
            findings.append(("WARN", word_count, first_60))
        elif word_count > 40:
            findings.append(("NOTE", word_count, first_60))
    return findings


def _check_multi_parentheses(sentences: list[str]) -> list[tuple[str, int, str]]:
    """Check for sentences with 2+ parenthetical pairs.

    Returns list of (level, pair_count, first_60_chars) tuples.
    """
    findings = []
    for sentence in sentences:
        pair_count = _count_parenthetical_pairs(sentence)
        if pair_count >= 2:
            first_60 = (sentence[:60] + "...") if len(sentence) > 60 else sentence
            findings.append(("WARN", pair_count, first_60))
    return findings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sentence complexity advisory checker"
    )
    parser.add_argument(
        "draft_dir",
        help="Path to draft directory (scans 02_results.md, 03_discussion.md, etc.)",
    )
    args = parser.parse_args(argv)

    draft_dir = Path(args.draft_dir).expanduser().resolve()

    # Files to scan.
    section_files = [
        "02_results.md",
        "03_discussion.md",
        "04_introduction.md",
        "05_abstract.md",
    ]

    all_findings_50 = []  # (file, line_approx, word_count, text)
    all_findings_paren = []  # (file, line_approx, pair_count, text)
    all_findings_40 = []  # (file, line_approx, word_count, text)
    total_sentences = 0
    files_scanned = 0

    for file_name in section_files:
        file_path = draft_dir / file_name
        if not file_path.is_file():
            continue

        text = file_path.read_text(encoding="utf-8")
        sentences = _split_into_sentences(text)
        total_sentences += len(sentences)
        files_scanned += 1

        # Check sentence length.
        for sentence in sentences:
            word_count = _count_words(sentence)
            first_60 = (sentence[:60] + "...") if len(sentence) > 60 else sentence

            if word_count > 50:
                # Estimate line number by counting newlines up to sentence.
                line_approx = text[: text.find(sentence)].count("\n") + 1
                all_findings_50.append((file_name, line_approx, word_count, first_60))
            elif word_count > 40:
                line_approx = text[: text.find(sentence)].count("\n") + 1
                all_findings_40.append((file_name, line_approx, word_count, first_60))

            # Check parenthetical pairs.
            pair_count = _count_parenthetical_pairs(sentence)
            if pair_count >= 2:
                line_approx = text[: text.find(sentence)].count("\n") + 1
                all_findings_paren.append((file_name, line_approx, pair_count, first_60))

    # Emit findings to stderr.
    warn_count_50 = len(all_findings_50)
    warn_count_paren = len(all_findings_paren)
    note_count = len(all_findings_40)

    for file_name, line_approx, word_count, text in all_findings_50:
        print(
            f"WARN  {file_name}:{line_approx}  sentence >50 words ({word_count}): "
            f'"{text}"',
            file=sys.stderr,
        )

    for file_name, line_approx, pair_count, text in all_findings_paren:
        print(
            f"WARN  {file_name}:{line_approx}  2+ parenthetical pairs ({pair_count}): "
            f'"{text}"',
            file=sys.stderr,
        )

    for file_name, line_approx, word_count, text in all_findings_40:
        print(
            f"NOTE  {file_name}:{line_approx}  sentence >40 words ({word_count}): "
            f'"{text}"',
            file=sys.stderr,
        )

    # Summary to stderr.
    total_warn = warn_count_50 + warn_count_paren
    if total_warn > 0 or note_count > 0:
        print(
            f"\ncheck_sentence_complexity: {total_warn} WARN, {note_count} NOTE "
            f"across {total_sentences} sentences in {files_scanned} files",
            file=sys.stderr,
        )
    else:
        print(
            f"check_sentence_complexity: all checks passed "
            f"({total_sentences} sentences in {files_scanned} files)",
            file=sys.stderr,
        )

    # JSON diagnostics to stdout.
    diagnostics = {
        "total_sentences": total_sentences,
        "files_scanned": files_scanned,
        "warn_over_50": warn_count_50,
        "warn_multi_paren": warn_count_paren,
        "note_over_40": note_count,
    }
    print(json.dumps(diagnostics))

    # Always exit 0 — advisory only.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
