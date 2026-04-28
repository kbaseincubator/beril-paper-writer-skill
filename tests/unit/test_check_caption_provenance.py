"""Tests for skill/tools/check_caption_provenance.py — Source 4 caption
fabrication detector (v0.4 Phase 4b).

Coverage:
  - Pure check helpers: numerical claim trace, named entity trace,
    panel-letter hallucination, word count compliance.
  - _flatten_bundle_text: includes all expected fields.
  - End-to-end: main() against a draft_dir with metadata.json +
    caption files. Always exits 0; emits WARN/NOTE to stderr.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[2]
_CHECKER = (
    _REPO_ROOT / "src" / "beril_paper_writer" / "skill" / "tools"
    / "check_caption_provenance.py"
)


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_caption_provenance_under_test", _CHECKER,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_caption_provenance_under_test"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def cc():
    return _load_checker()


# ---------------------------------------------------------------------------
# _flatten_bundle_text
# ---------------------------------------------------------------------------


def test_flatten_bundle_includes_all_descriptor_fields(cc) -> None:
    bundle = {
        "short_caption": "Short.",
        "structured_descriptor": {
            "title": "Title text",
            "axes_labels": ["X axis", "Y axis"],
            "legend_labels": ["L1", "L2"],
            "panels": [
                {"letter": "A", "title": "Panel A title",
                 "xlabel": "Pa-x", "ylabel": "Pa-y",
                 "prose_context": "Panel A prose"},
            ],
            "notebook_prose": "Notebook prose content.",
        },
        "prose_panel_callouts": {"A": "Callout context for A"},
        "report_prose": "REPORT context.",
        "results_section_prose": "Results context.",
        "max_words": 200,
    }
    flat = cc._flatten_bundle_text(bundle)
    for needle in [
        "Title text", "X axis", "Y axis", "L1", "L2",
        "Panel A title", "Pa-x", "Pa-y", "Panel A prose",
        "Notebook prose content.", "Callout context for A",
        "REPORT context.", "Results context.", "Short.",
    ]:
        assert needle in flat, f"missing: {needle!r}"


def test_flatten_bundle_handles_empty_fields(cc) -> None:
    bundle = {
        "structured_descriptor": {
            "title": None,
            "axes_labels": [],
            "panels": [],
            "notebook_prose": None,
        },
    }
    flat = cc._flatten_bundle_text(bundle)
    assert flat == ""


# ---------------------------------------------------------------------------
# check_numerical_claims
# ---------------------------------------------------------------------------


class TestNumericalClaims:
    def test_grounded_numbers_no_warnings(self, cc):
        caption = "We measured 95 of 3,705 dark genes (2.6%) showed a strong phenotype (|fit| > 2)."
        corpus = "95 of the 3,705 dark genes with fitness data (2.6%) at threshold 2"
        warnings = cc.check_numerical_claims(caption, corpus)
        assert warnings == []

    def test_ungrounded_number_warns(self, cc):
        caption = "We measured n = 100 samples."
        corpus = "We had a few samples."
        warnings = cc.check_numerical_claims(caption, corpus)
        assert len(warnings) == 1
        assert "100" in warnings[0]

    def test_comma_normalization_works(self, cc):
        caption = "Across 1,000 samples"
        corpus = "Across 1000 samples"  # no comma in corpus
        warnings = cc.check_numerical_claims(caption, corpus)
        assert warnings == []

    def test_comma_in_caption_corpus_match(self, cc):
        caption = "Across 1000 samples"  # no comma in caption
        corpus = "We had 1,000 in our cohort."
        warnings = cc.check_numerical_claims(caption, corpus)
        assert warnings == []

    def test_percent_suffix_grounded(self, cc):
        caption = "(2.6%)"
        corpus = "showed strong phenotypes (2.6% of cohort)"
        warnings = cc.check_numerical_claims(caption, corpus)
        assert warnings == []

    def test_dedup_repeated_token(self, cc):
        caption = "Saw 100 in panel A and 100 in panel B."
        corpus = "We had only 50 samples."
        warnings = cc.check_numerical_claims(caption, corpus)
        # Only one WARN even though "100" appears twice in caption.
        assert len(warnings) == 1


# ---------------------------------------------------------------------------
# check_named_entities
# ---------------------------------------------------------------------------


class TestNamedEntities:
    def test_grounded_entity_no_warning(self, cc):
        caption = "Pseudomonas aeruginosa cultures were grown."
        corpus = "Pseudomonas aeruginosa was used in this study."
        warnings = cc.check_named_entities(caption, corpus)
        assert warnings == []

    def test_ungrounded_entity_warns(self, cc):
        caption = "Escherichia Coli cultures were grown."
        corpus = "Pseudomonas aeruginosa was used in this study."
        warnings = cc.check_named_entities(caption, corpus)
        # Both "Escherichia Coli" not in corpus → WARN
        assert any("Escherichia Coli" in w for w in warnings)

    def test_allow_list_skips_common_phrases(self, cc):
        caption = "Each Panel shows a different condition."
        corpus = "Some prose without that phrase."
        warnings = cc.check_named_entities(caption, corpus)
        # "Each Panel" is in the allow-list
        assert warnings == []


# ---------------------------------------------------------------------------
# check_panel_letters
# ---------------------------------------------------------------------------


class TestPanelLetters:
    def test_grounded_in_descriptor_panels(self, cc):
        caption = "(A) shows distribution. (B) shows breadth."
        bundle = {
            "structured_descriptor": {
                "panels": [
                    {"letter": "A"},
                    {"letter": "B"},
                ],
            },
        }
        warnings = cc.check_panel_letters(caption, bundle)
        assert warnings == []

    def test_grounded_in_prose_callouts(self, cc):
        caption = "(A) shows distribution. (B) shows breadth."
        bundle = {
            "structured_descriptor": {"panels": []},
            "prose_panel_callouts": {"A": "...", "B": "..."},
        }
        warnings = cc.check_panel_letters(caption, bundle)
        assert warnings == []

    def test_ungrounded_letter_warns(self, cc):
        caption = "(A) shows X. (B) shows Y. (C) shows Z."
        bundle = {
            "structured_descriptor": {
                "panels": [{"letter": "A"}, {"letter": "B"}],
            },
            "prose_panel_callouts": {},
        }
        warnings = cc.check_panel_letters(caption, bundle)
        assert len(warnings) == 1
        assert "(C)" in warnings[0]

    def test_panel_word_form_recognized(self, cc):
        caption = "Panel A shows X. panel labeled C also visible."
        bundle = {
            "structured_descriptor": {
                "panels": [{"letter": "A"}],
            },
            "prose_panel_callouts": {},
        }
        warnings = cc.check_panel_letters(caption, bundle)
        # "Panel A" is grounded; "panel labeled C" is not.
        assert len(warnings) == 1
        assert "(C)" in warnings[0]

    def test_no_panels_in_caption_no_warnings(self, cc):
        caption = "This figure shows nothing about panels."
        bundle = {
            "structured_descriptor": {"panels": []},
            "prose_panel_callouts": {},
        }
        assert cc.check_panel_letters(caption, bundle) == []


# ---------------------------------------------------------------------------
# check_word_count
# ---------------------------------------------------------------------------


class TestWordCount:
    def test_in_range_no_warning(self, cc):
        caption = " ".join(["word"] * 50)
        assert cc.check_word_count(caption) == []

    def test_under_minimum_warns(self, cc):
        caption = "Five word caption here only."
        warnings = cc.check_word_count(caption)
        assert len(warnings) == 1
        assert "below minimum" in warnings[0]

    def test_over_maximum_warns(self, cc):
        caption = " ".join(["word"] * 250)
        warnings = cc.check_word_count(caption)
        assert len(warnings) == 1
        assert "above maximum" in warnings[0]

    def test_at_boundary_no_warning(self, cc):
        caption_min = " ".join(["x"] * cc.WORD_COUNT_MIN)
        caption_max = " ".join(["x"] * cc.WORD_COUNT_MAX)
        assert cc.check_word_count(caption_min) == []
        assert cc.check_word_count(caption_max) == []


# ---------------------------------------------------------------------------
# check_caption (full pipeline)
# ---------------------------------------------------------------------------


def test_check_caption_clean_synthetic(cc):
    """A fully grounded 50-word caption returns 0 warnings."""
    caption = (
        "Dark gene fitness distribution across 343 stress and metabolic "
        "conditions. (A) Magnitude density curves comparing 3,705 dark "
        "genes to 36,420 annotated genes; 95 of 3,705 dark genes (2.6%) "
        "showed a strong phenotype (|fit| > 2). (B) Distribution of "
        "phenotype-positive condition counts; right-skewed."
    )
    bundle = {
        "structured_descriptor": {
            "title": "Dark gene fitness distribution",
            "panels": [
                {"letter": "A", "title": "Magnitude distribution"},
                {"letter": "B", "title": "Condition breadth"},
            ],
            "notebook_prose": (
                "We compared 3,705 dark genes (no annotation) to 36,420 "
                "annotated genes across 343 stress + metabolic conditions. "
                "Density curves use Scott's rule for bandwidth."
            ),
        },
        "prose_panel_callouts": {
            "A": "(Fig. 3A) showed strong phenotypes (|fit| > 2)",
            "B": "(Fig. 3B) right-skewed",
        },
        "results_section_prose": (
            "Across 343 stress and metabolic conditions, 95 of 3,705 "
            "dark genes (2.6%) showed a strong phenotype (|fit| > 2)."
        ),
    }
    warnings = cc.check_caption(caption, bundle)
    assert warnings == [], f"unexpected warnings: {warnings}"


def test_check_caption_catches_fabricated_n(cc):
    caption = (
        "Dark gene distribution across conditions. (A) Comparing 999 dark "
        "genes to 11,111 annotated. (B) Distribution skewed. " * 2
    )
    bundle = {
        "structured_descriptor": {
            "panels": [{"letter": "A"}, {"letter": "B"}],
            "notebook_prose": "We compared 3,705 dark genes to 36,420 annotated.",
        },
    }
    warnings = cc.check_caption(caption, bundle)
    # Should flag both 999 and 11,111 (or 11111)
    assert any("999" in w for w in warnings)
    assert any("11,111" in w or "11111" in w for w in warnings)


def test_check_caption_catches_fabricated_panel(cc):
    caption = (
        "Distribution shown across conditions. (A) Magnitude. (Z) Spurious panel. "
        "Description continues with more padding text to reach minimum word "
        "count of thirty so the count check does not trigger."
    )
    bundle = {
        "structured_descriptor": {
            "panels": [{"letter": "A"}],
        },
        "prose_panel_callouts": {},
    }
    warnings = cc.check_caption(caption, bundle)
    assert any("(Z)" in w for w in warnings)


# ---------------------------------------------------------------------------
# End-to-end: main() against a draft_dir with metadata
# ---------------------------------------------------------------------------


def _build_draft_with_metadata(
    tmp_path: Path,
    captions_metadata: list[dict],
    caption_files: dict[str, str],
) -> Path:
    """Create a minimal draft dir with audit/metadata + caption files.

    captions_metadata is the 'captions' list to embed in metadata.json.
    caption_files maps relative-path → caption-text.
    """
    draft = tmp_path / "draft_x"
    audit = draft / "audit"
    audit.mkdir(parents=True)
    metadata = {"schema_version": 1, "captions": captions_metadata}
    (audit / "figure_caption.v1.metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8",
    )
    for rel_path, content in caption_files.items():
        target = draft / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return draft


def test_main_with_no_metadata_returns_0_with_note(cc, tmp_path, capsys):
    draft = tmp_path / "draft_x"
    draft.mkdir()
    rc = cc.main([str(draft)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "NOTE" in err
    assert "phase_caption_synthesis did not run" in err


def test_main_with_clean_caption_no_warnings(cc, tmp_path, capsys):
    bundle = {
        "structured_descriptor": {
            "title": "Test",
            "panels": [{"letter": "A"}],
            "notebook_prose": "We measured 50 conditions.",
        },
        "prose_panel_callouts": {},
    }
    caption_text = (
        "Test figure showing 50 conditions across the experiment. "
        "(A) Distribution of magnitude values for the cohort. "
        "Density estimation uses standard kernel methods with default "
        "bandwidth. Cohort size of 50 samples represents the working "
        "dataset analyzed in these working analyses across all conditions."
    )
    draft = _build_draft_with_metadata(
        tmp_path,
        [{
            "figure_id": 1,
            "output_path": "audit/figure_caption_1.md",
            "input_bundle": bundle,
            "source_chosen": "llm",
        }],
        {"audit/figure_caption_1.md": caption_text},
    )
    rc = cc.main([str(draft)])
    assert rc == 0
    err = capsys.readouterr().err
    # No WARN lines (only summary)
    assert "summary: 1 captions checked, 0 WARN" in err


def test_main_skips_deterministic_source(cc, tmp_path, capsys):
    """source_chosen='deterministic' entries are NOT checked."""
    bundle = {"structured_descriptor": {"panels": []}}
    draft = _build_draft_with_metadata(
        tmp_path,
        [{
            "figure_id": 1,
            "output_path": "audit/figure_caption_1.md",
            "input_bundle": bundle,
            "source_chosen": "deterministic",
        }],
        {"audit/figure_caption_1.md": "Anything goes here, even fabrications."},
    )
    rc = cc.main([str(draft)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "0 captions checked" in err


def test_main_warns_on_fabrication(cc, tmp_path, capsys):
    bundle = {
        "structured_descriptor": {
            "panels": [{"letter": "A"}],
            "notebook_prose": "We measured 50 conditions.",
        },
    }
    # Caption fabricates n=999 (not in bundle) AND mentions panel Z
    caption_text = (
        "Test figure. (A) Distribution shown. (Z) Fabricated panel. "
        "We had 999 conditions in this analysis. " * 3
    )
    draft = _build_draft_with_metadata(
        tmp_path,
        [{
            "figure_id": 1,
            "output_path": "audit/figure_caption_1.md",
            "input_bundle": bundle,
            "source_chosen": "llm",
        }],
        {"audit/figure_caption_1.md": caption_text},
    )
    rc = cc.main([str(draft)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "999" in err
    assert "(Z)" in err
    assert "1 captions checked" in err
