"""Tests for paper_writer_helpers.py embed-figures helpers (v0.3 Tier 2.2).

Coverage:
  - `_find_sentence_end_after` heuristic on tricky cases:
    * `Fig. 5` internal period not treated as sentence end
    * `e.g.` lowercase followup not treated as sentence end
    * `(Fig. 3).` form (closing paren before terminator)
    * End-of-string + newline
  - `_embed_figures_in_text` core behavior:
    * Single (Fig. N) callout → image tag injected after sentence
    * Multi-figure sentence → multiple tags injected after same sentence
    * Idempotent: re-running does not double-inject
    * Skipped when N has no manifest entry
    * Multiple Ns across different sentences → independent injections
  - End-to-end `cmd_embed_figures` against a synthetic draft fixture.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[2]
_HELPERS = (
    _REPO_ROOT / "src" / "beril_paper_writer" / "skill" / "tools"
    / "paper_writer_helpers.py"
)


def _load_helpers():
    spec = importlib.util.spec_from_file_location(
        "paper_writer_helpers_under_test", _HELPERS
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["paper_writer_helpers_under_test"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def helpers():
    return _load_helpers()


# ---------------------------------------------------------------------------
# Sentence-end heuristic
# ---------------------------------------------------------------------------


def test_sentence_end_simple(helpers) -> None:
    text = "First sentence. Second sentence."
    pos = helpers._find_sentence_end_after(text, 0)
    # First `.` is at index 14; followed by ` ` then uppercase `S` → sentence end.
    assert pos == 15  # right after the `.`


def test_sentence_end_skips_fig_n_internal_period(helpers) -> None:
    """The `.` in `Fig. 5` (followed by space + digit) is NOT a sentence end."""
    text = "Cite (Fig. 5) and continue. Next sentence."
    pos = helpers._find_sentence_end_after(text, 0)
    # The first `.` (in `Fig. 5`) is followed by ` ` then `5` (digit) — not a
    # sentence end. The next `.` is after `continue` — followed by ` N` (upper).
    expected = text.index("continue.") + len("continue.")
    assert pos == expected


def test_sentence_end_skips_eg_lowercase(helpers) -> None:
    """`e.g.` followed by lowercase is NOT a sentence end."""
    text = "We use methods e.g. similarity scoring. Next sentence."
    pos = helpers._find_sentence_end_after(text, 0)
    # First `.` (in `e.`) → followed by `g` (lowercase) — not sentence end.
    # Second `.` (in `e.g.`) → followed by ` s` (lowercase) — not sentence end.
    # Third `.` (after `scoring`) → followed by ` N` (uppercase) — yes.
    expected = text.index("scoring.") + len("scoring.")
    assert pos == expected


def test_sentence_end_after_paren(helpers) -> None:
    """`(Fig. 3).` form: the terminating `.` is after the closing paren."""
    text = "Stable point (Fig. 3). Next sentence."
    pos = helpers._find_sentence_end_after(text, 0)
    expected = text.index(").") + len(").")
    assert pos == expected


def test_sentence_end_eof(helpers) -> None:
    """End-of-string returns len(text) when no terminator found."""
    text = "trailing prose with no sentence end"
    pos = helpers._find_sentence_end_after(text, 0)
    assert pos == len(text)


def test_sentence_end_newline_terminator(helpers) -> None:
    """`.` immediately followed by `\\n` is a sentence end (paragraph break)."""
    text = "End of paragraph.\n\nNew paragraph."
    pos = helpers._find_sentence_end_after(text, 0)
    expected = text.index("paragraph.") + len("paragraph.")
    assert pos == expected


# ---------------------------------------------------------------------------
# Embed core
# ---------------------------------------------------------------------------


def _basic_figure_map():
    return {
        1: ("fig01_a.png", "Caption for figure one"),
        2: ("fig02_b.png", "Caption for figure two"),
        3: ("fig03_c.png", "Caption for figure three"),
        5: ("fig05_e.png", "Caption for figure five"),
    }


def test_embed_single_callout(helpers) -> None:
    text = "Stable point (Fig. 1). Next sentence."
    new_text, injected, skipped = helpers._embed_figures_in_text(
        text, _basic_figure_map()
    )
    assert 1 in injected
    assert "![Figure 1: Caption for figure one](figures/fig01_a.png)" in new_text
    assert skipped == []
    # Injection lands AFTER the period, before "Next sentence."
    fig_pos = new_text.index("![Figure 1:")
    period_pos = new_text.index("(Fig. 1).") + len("(Fig. 1).")
    assert fig_pos > period_pos


def test_embed_multi_figure_sentence(helpers) -> None:
    text = "Both apply (Fig. 3 and Fig. 5). Next sentence."
    new_text, injected, skipped = helpers._embed_figures_in_text(
        text, _basic_figure_map()
    )
    assert set(injected.keys()) == {3, 5}
    # Both image tags should appear, in N-ascending order, before "Next sentence."
    fig3_pos = new_text.index("![Figure 3:")
    fig5_pos = new_text.index("![Figure 5:")
    next_pos = new_text.index("Next sentence")
    assert fig3_pos < fig5_pos < next_pos


def test_embed_idempotent(helpers) -> None:
    """Re-running does not double-inject."""
    text = "Stable point (Fig. 1). Next."
    once, _, _ = helpers._embed_figures_in_text(text, _basic_figure_map())
    twice, injected, _ = helpers._embed_figures_in_text(once, _basic_figure_map())
    assert once == twice
    assert injected == {}  # nothing injected on the second pass


def test_embed_skips_n_not_in_manifest(helpers) -> None:
    """A (Fig. 9) callout when 9 isn't in the manifest is skipped + flagged."""
    text = "Cite (Fig. 9) here. Next."
    new_text, injected, skipped = helpers._embed_figures_in_text(
        text, _basic_figure_map()
    )
    assert injected == {}
    assert skipped == [9]
    assert new_text == text  # no modification


def test_embed_first_occurrence_only(helpers) -> None:
    """Only the FIRST (Fig. N) per N is embedded; later citations stay textual."""
    text = "First cite (Fig. 1). Second sentence cites (Fig. 1) again."
    new_text, injected, _ = helpers._embed_figures_in_text(
        text, _basic_figure_map()
    )
    # Exactly one image tag.
    assert new_text.count("![Figure 1:") == 1
    # The image tag is between the first and second occurrences.
    fig_pos = new_text.index("![Figure 1:")
    first_callout = new_text.index("(Fig. 1)")
    # Second callout is after first; image tag is between them.
    second_callout = new_text.index("(Fig. 1)", first_callout + 1)
    assert first_callout < fig_pos < second_callout


def test_embed_multiple_sections_separate_injections(helpers) -> None:
    """Multiple distinct sentences with different Ns each get their own embed."""
    text = (
        "First sentence cites (Fig. 1). "
        "Second sentence cites (Fig. 2). "
        "Third sentence cites (Fig. 3)."
    )
    new_text, injected, _ = helpers._embed_figures_in_text(
        text, _basic_figure_map()
    )
    assert set(injected.keys()) == {1, 2, 3}
    # Image tags appear in document order (1, 2, 3).
    assert new_text.index("![Figure 1:") < new_text.index("![Figure 2:")
    assert new_text.index("![Figure 2:") < new_text.index("![Figure 3:")


# ---------------------------------------------------------------------------
# End-to-end: cmd_embed_figures against a synthetic draft fixture
# ---------------------------------------------------------------------------


def _build_synthetic_draft(tmp_path: Path) -> Path:
    """Build a minimal draft directory with manifest, inventory, and a
    single section file containing two callouts."""
    draft = tmp_path / "draft_x"
    (draft / "figures").mkdir(parents=True)
    # Synthetic figure files (just touch — embed step doesn't read them)
    (draft / "figures" / "fig01_a.png").write_bytes(b"")
    (draft / "figures" / "fig02_b.png").write_bytes(b"")
    # Manifest
    (draft / "figures_manifest.tsv").write_text(
        "paper_order_n\tfilename\tinventory_lookup_name\n"
        "1\tfig01_a.png\torig_one.png\n"
        "2\tfig02_b.png\torig_two.png\n",
        encoding="utf-8",
    )
    # Inventory with caption candidates
    (draft / "figures_inventory.md").write_text(
        "# Inventory\n\n"
        "### `figures/orig_one.png`\n\n"
        "_PNG, 1 KB_\n\n"
        "**Caption candidates:**\n\n"
        "- **REPORT.md _(in Key Findings)_**: Caption for figure one\n"
        "- **filename**: Orig one\n\n"
        "### `figures/orig_two.png`\n\n"
        "_PNG, 1 KB_\n\n"
        "**Caption candidates:**\n\n"
        "- **REPORT.md _(in Key Findings)_**: Caption for figure two\n"
        "- **filename**: Orig two\n",
        encoding="utf-8",
    )
    # Section file with two callouts
    (draft / "02_results.md").write_text(
        "# Results\n\n"
        "First subsection cites (Fig. 1). Next sentence here.\n\n"
        "Second subsection cites (Fig. 2). End of section.\n",
        encoding="utf-8",
    )
    return draft


def test_cmd_embed_figures_end_to_end(helpers, tmp_path: Path, capsys) -> None:
    draft = _build_synthetic_draft(tmp_path)

    class Args:
        draft_dir = str(draft)

    rc = helpers.cmd_embed_figures(Args())
    assert rc == 0
    captured = capsys.readouterr()
    # Stdout summary mentions 2 embedded.
    assert "embedded: 2" in captured.out

    # Section file now contains both image tags with REPORT-derived captions.
    body = (draft / "02_results.md").read_text(encoding="utf-8")
    assert "![Figure 1: Caption for figure one](figures/fig01_a.png)" in body
    assert "![Figure 2: Caption for figure two](figures/fig02_b.png)" in body

    # Re-running is idempotent.
    capsys.readouterr()  # clear
    rc2 = helpers.cmd_embed_figures(Args())
    assert rc2 == 0
    captured2 = capsys.readouterr()
    assert "embedded: 0" in captured2.out  # nothing new on second pass
    body_after = (draft / "02_results.md").read_text(encoding="utf-8")
    assert body == body_after


def test_cmd_embed_figures_missing_manifest(helpers, tmp_path: Path, capsys) -> None:
    """No manifest → NOTE + zero embeds, never errors."""
    (tmp_path / "draft_y").mkdir()

    class Args:
        draft_dir = str(tmp_path / "draft_y")

    rc = helpers.cmd_embed_figures(Args())
    assert rc == 0
    captured = capsys.readouterr()
    assert "embedded: 0" in captured.out
