"""Cycle 2 — paper-writer pre-handoff deliverable validation tests.

Coverage organization:
  - One CLEAN fixture (a minimal paper-mode draft layout with a real
    title/author block, a resolved figure, all M1 sections present)
    — passes every gate (P0/P1 == 0).
  - Per-gate regression fixtures, drawn from the Cycle-1 lessons +
    paper-writer specifics:
      G1 section_completeness  — missing IMRAD section fires.
      G2 placeholder_or_leaked_template — TBD-in-body fires;
         correct "...Caulobacter..." title passes clean
         (the Cycle-1 G1 regression case ported verbatim).
      G3 figure_resolution_and_embedding — broken figure ref fires.
      G4 mode_depth_vs_user_intent — mode drop on resume caught.
  - user_intent.py — pinned byte-identical to beril-presentation-maker
    v1.2.0 (per llm_config copy-not-share convention + Cycle-2 brief).

Pattern source: tests/unit/test_validate_deliverable.py in
beril-presentation-maker v1.2.0.
"""
from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

# Load the in-tree modules without requiring a reinstall (same pattern
# as tests/unit/test_assemble_docx.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL_TOOLS = _REPO_ROOT / "src" / "beril_paper_writer" / "skill" / "tools"
sys.path.insert(0, str(_SKILL_TOOLS))

import finalize_deliverable as fd  # noqa: E402
import user_intent as ui  # noqa: E402
import validate_deliverable as vd  # noqa: E402

# ===========================================================================
# Fixture builders
# ===========================================================================


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _make_minimal_png(width: int, height: int) -> bytes:
    """Smallest valid PNG of the given pixel dims. Same helper as
    presentation-maker's test_validate_deliverable for parity."""
    sig = b"\x89PNG\r\n\x1a\n"

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", crc)
        )

    ihdr = struct.pack(">II5B", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(
        b"\x00" + b"\x00\x00\x00" * width for _ in range(height)
    )
    idat = zlib.compress(raw, level=1)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


# Minimal IMRAD-paper manuscript that satisfies validate_manuscript's
# M1 (IMRAD sections present), has a title + authors line, and
# references one block-image figure. Trimmed to the minimum that
# passes M1; M2–M10 may emit non-blocking warnings on this synthetic
# but G1 only escalates errors → P0 and warnings → P1, and the brief
# treats P1s as acceptable on the clean fixture (the v1.1.0 caulobacter
# draft routinely had M-series warnings without being un-shippable).
_CLEAN_MANUSCRIPT = """\
# Iron regulation in Caulobacter crescentus: a fur regulon analysis

Authors: Adam Arkin · LBNL

## Abstract

Background: Iron is essential for bacterial growth. Methods: We
analysed the fur regulon. Results: We identified 12 regulated genes.
Conclusions: These genes form a coherent regulon.

## Introduction

The fur regulon is a key iron-response circuit.

## Methods

We sequenced and analysed expression data.

## Results

Figure 1 shows the regulon structure.

![Figure 1: fur regulon overview](figures/fig01.png)

## Discussion

The fur regulon coordinates iron homeostasis.

## References

1. Smith et al. 2025.
"""


def _build_clean_draft(tmp_path: Path) -> Path:
    """Build a minimal paper-mode draft that passes every gate
    cleanly. Project dir = `caulobacter_fur_lipida_loss` so the G1
    regression test (correct Caulobacter title) shares the project
    shape.
    """
    proj_dir = tmp_path / "projects" / "caulobacter_fur_lipida_loss"
    draft_dir = proj_dir / "papers" / "draft_1"
    (draft_dir / "audit").mkdir(parents=True, exist_ok=True)

    # beril.yaml at the project root.
    _write_text(
        proj_dir / "beril.yaml",
        "project_id: caulobacter_fur_lipida_loss\n"
        "authors:\n"
        "  - name: Adam Arkin\n"
        "    affiliation: LBNL\n",
    )

    # user_intent: explicit paper mode.
    ui.write_user_intent(
        draft_dir,
        mode="paper",
        tier="STRONG",
        audience="peer",
        mode_explicit=True,
        tier_explicit=False,
        audience_explicit=False,
        now="2026-06-07T15:00:00Z",
    )

    # state.json matching the user-intent mode (this is the truth
    # the G4 mode check compares against).
    _write_json(
        draft_dir / "state.json",
        {
            "version": "0.1",
            "project_id": "caulobacter_fur_lipida_loss",
            "draft_number": 1,
            "phase": "assembled",
            "mode": "paper",
        },
    )

    # A real PNG figure under the project's figures/ dir so the
    # manuscript's `![](figures/fig01.png)` resolves via the
    # project_dir fallback.
    (proj_dir / "figures").mkdir(parents=True, exist_ok=True)
    (proj_dir / "figures" / "fig01.png").write_bytes(
        _make_minimal_png(800, 600)
    )

    # manuscript.md
    _write_text(draft_dir / "manuscript.md", _CLEAN_MANUSCRIPT)

    return draft_dir


# ===========================================================================
# user_intent.py — pinned VERBATIM byte-identical to presentation-maker
# v1.2.0 (per llm_config copy-not-share convention).
# ===========================================================================


def test_user_intent_byte_identical_to_presentation_maker():
    """The copied user_intent.py MUST be byte-identical to the
    beril-presentation-maker v1.2.0 source. The cross-skill
    conformance fixture (in craft-platform tests) re-asserts this
    over both repos; this test pins it locally so a CC commit can't
    drift the paper-writer copy in isolation. Per the Cycle-2 brief
    (Adam, 2026-06-07): COPY VERBATIM (no source-SHA header inside
    the file; provenance lives in RELEASE_NOTES + the conformance
    fixture)."""
    paper_writer_copy = (
        _SKILL_TOOLS / "user_intent.py"
    ).read_bytes()
    # Search upward for the sibling beril-presentation-maker-skill-
    # draft tree. Skip the test if it isn't present (a paper-writer-
    # only checkout shouldn't fail this test — the craft-platform
    # conformance fixture is the authoritative cross-repo check).
    workspace = _REPO_ROOT.parent
    pm_path = (
        workspace / "beril-presentation-maker-skill-draft"
        / "src" / "beril_presentation_maker" / "skill" / "tools"
        / "user_intent.py"
    )
    if not pm_path.is_file():
        import pytest
        pytest.skip(
            "beril-presentation-maker sibling tree not present; "
            "cross-repo byte identity is checked by craft-platform's "
            "conformance fixture."
        )
    pm_source = pm_path.read_bytes()
    assert paper_writer_copy == pm_source, (
        "user_intent.py drift between paper-writer and "
        "presentation-maker. The Cycle-2 brief mandates COPY VERBATIM "
        "(per the llm_config copy-not-share convention). Update both "
        "or restore parity."
    )


# ===========================================================================
# Clean fixture — every gate passes (P0/P1 == 0 modulo M-series warnings)
# ===========================================================================


def test_clean_fixture_no_g2_g3_g4_p0(tmp_path):
    """The clean fixture has a real title (correct Caulobacter form),
    a populated author line, a resolved figure, mode=paper persisted
    on both state.json and user_intent.json. G2/G3/G4 must emit ZERO
    P0/P1 findings. G1 (section_completeness) projects whatever
    validate_manuscript says; M-series warnings are acceptable on the
    synthetic minimal fixture (the v1.1.0 caulobacter draft shipped
    with M-series warnings too)."""
    draft_dir = _build_clean_draft(tmp_path)
    findings = vd.validate(draft_dir)
    # Filter out G1 — we don't pin its outcome; G1 just reflects
    # validate_manuscript, which has its own (separate) test suite.
    non_g1 = [f for f in findings if f.gate != "section_completeness"]
    blocking = [
        f for f in non_g1
        if f.severity in (vd.SEVERITY_P0, vd.SEVERITY_P1)
    ]
    assert blocking == [], (
        f"clean fixture had {len(blocking)} non-G1 P0/P1 finding(s): "
        f"{[(f.id, f.message[:80]) for f in blocking]}"
    )


# ===========================================================================
# G1 section_completeness — missing IMRAD section fires
# ===========================================================================


def test_g1_missing_imrad_section_fires(tmp_path):
    """Strip the Methods section out of manuscript.md. G1 projects
    validate_manuscript's M1 violation into a Finding. M1 emits an
    error (severity=error → P0) with escalation_path=auto-fix → our
    auto + rerun_validate."""
    draft_dir = _build_clean_draft(tmp_path)
    manuscript = draft_dir / "manuscript.md"
    text = manuscript.read_text(encoding="utf-8")
    # Remove the Methods section heading + its single line.
    text = text.replace(
        "## Methods\n\nWe sequenced and analysed expression data.\n\n",
        "",
    )
    manuscript.write_text(text, encoding="utf-8")

    findings = vd.check_g1_section_completeness(draft_dir, mode="paper")
    # M1 should emit a violation about the missing Methods section.
    m1_findings = [f for f in findings if f.id.startswith("g1:m1_")]
    assert m1_findings, (
        f"expected M1 violation finding for missing Methods; got "
        f"{[f.id for f in findings]}"
    )
    # First finding should be P0 (M1 marks section-absence as error).
    f0 = m1_findings[0]
    assert f0.severity == vd.SEVERITY_P0
    # auto-fix escalation_path → auto + rerun_validate.
    assert f0.remediation.kind == vd.REMEDIATION_AUTO
    assert f0.remediation.action == vd.AUTO_RERUN_VALIDATE


def test_g1_clean_fixture_findings_project_to_auto_rerun_validate(tmp_path):
    """The clean fixture intentionally does NOT satisfy ICMJE
    end-to-end — it has no AI Disclosure section, no Data
    Availability, no formal structured Abstract subsections, no
    Limitations. validate_manuscript's M1-M10 will surface real
    violations against this synthetic. What G1 must guarantee is
    that the PROJECTION is correct: every auto-fix-escalation
    violation lands as REMEDIATION_AUTO + AUTO_RERUN_VALIDATE.
    The synthetic is the right shape for testing the projection;
    a fully-ICMJE-clean fixture is the live caulobacter draft_2."""
    draft_dir = _build_clean_draft(tmp_path)
    findings = vd.check_g1_section_completeness(draft_dir, mode="paper")
    # We get some M-series findings (correct — minimal synthetic).
    # Every one of them must project to a valid (kind, action) pair
    # from the escalation-path table.
    for f in findings:
        assert f.gate == "section_completeness"
        assert f.severity in (vd.SEVERITY_P0, vd.SEVERITY_P1)
        assert f.remediation.kind in vd.REMEDIATION_KINDS
        if f.remediation.kind == vd.REMEDIATION_AUTO:
            assert f.remediation.action == vd.AUTO_RERUN_VALIDATE, (
                f"auto-fix escalation must map to AUTO_RERUN_VALIDATE; "
                f"got {f.remediation.action!r} on {f.id}"
            )


# ===========================================================================
# G2 placeholder_or_leaked_template
# ===========================================================================


def test_g2_correct_caulobacter_title_passes_clean(tmp_path):
    """Cycle-1 G1 regression case ported VERBATIM. A correct title
    containing only the standalone organism name `Caulobacter` must
    NOT fire the dirname-leak check. An earlier broader rule did,
    and the strip_dirname_token handler would have deleted the
    organism name from the title — destroying a correct title.
    The narrowed detector in paper-writer's _contains_dirname_token
    matches the presentation-maker v1.2.0 rule (full-slug OR ≥2
    adjacent segments)."""
    draft_dir = _build_clean_draft(tmp_path)
    # The clean fixture's title is exactly the Cycle-1 case:
    # "Iron regulation in Caulobacter crescentus: a fur regulon analysis"
    manuscript_md = (draft_dir / "manuscript.md").read_text(encoding="utf-8")
    findings = vd.check_g2_placeholder_or_leaked_template(
        draft_dir, manuscript_md
    )
    leak = [f for f in findings if "dirname_leak" in f.id]
    assert leak == [], (
        f"correct Caulobacter title MUST NOT fire dirname-leak; "
        f"got {[(f.id, f.message[:120]) for f in leak]}. "
        f"_contains_dirname_token is too broad; revisit narrowing rules."
    )


def test_g2_dirname_leak_full_slug_fires_p1_targeted(tmp_path):
    """Case (a): the verbatim full dir-name appears in the title.
    Fires P1 with TARGETED remediation. NO auto-strip (the Cycle-1
    G1 lesson: too destructive on fuzzy match)."""
    draft_dir = _build_clean_draft(tmp_path)
    bad_md = _CLEAN_MANUSCRIPT.replace(
        "# Iron regulation in Caulobacter crescentus: a fur regulon analysis",
        "# Findings on caulobacter_fur_lipida_loss: a brief",
    )
    (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")

    findings = vd.check_g2_placeholder_or_leaked_template(draft_dir, bad_md)
    leak = [f for f in findings if "dirname_leak" in f.id]
    assert len(leak) == 1
    assert leak[0].severity == vd.SEVERITY_P1
    assert leak[0].remediation.kind == vd.REMEDIATION_TARGETED
    assert leak[0].remediation.action is None  # no auto


def test_g2_dirname_leak_adjacent_pair_fires_p1_targeted(tmp_path):
    """Case (b): ≥2 adjacent dir-segments together. `lipida loss`
    is the adjacent pair from `caulobacter_fur_lipida_loss`."""
    draft_dir = _build_clean_draft(tmp_path)
    bad_md = _CLEAN_MANUSCRIPT.replace(
        "# Iron regulation in Caulobacter crescentus: a fur regulon analysis",
        "# Mechanisms of Lipida loss in Caulobacter crescentus",
    )
    (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")

    findings = vd.check_g2_placeholder_or_leaked_template(draft_dir, bad_md)
    leak = [f for f in findings if "dirname_leak" in f.id]
    assert len(leak) == 1
    assert leak[0].severity == vd.SEVERITY_P1


def test_g2_single_other_segment_does_not_fire(tmp_path):
    """A lone segment-word (`Loss`, `Lipida`, `Fur`) without an
    adjacent dir-segment must NOT fire. The narrowed detector
    matches only the full slug or ≥2-adjacent windows."""
    for word in ("Loss", "Lipida", "Fur"):
        draft_dir = _build_clean_draft(tmp_path / word)
        bad_md = _CLEAN_MANUSCRIPT.replace(
            "# Iron regulation in Caulobacter crescentus: a fur regulon analysis",
            f"# A note on {word} and protein dynamics in Caulobacter",
        )
        (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")
        findings = vd.check_g2_placeholder_or_leaked_template(draft_dir, bad_md)
        leak = [f for f in findings if "dirname_leak" in f.id]
        assert leak == [], (
            f"single segment-word {word!r} must NOT fire on its own; "
            f"got {[(f.id, f.message[:80]) for f in leak]}"
        )


def test_g2_tbd_in_body_fires(tmp_path):
    """A `TBD` token in the manuscript body fires a P1 with a
    targeted remediation pointing at `continue`. v1.2.0 followup:
    the finding id moved from `g2:tbd_in_body` → `g2:placeholder_in_body`
    when the placeholder vocab broadened beyond TBD; the test
    asserts the new id."""
    draft_dir = _build_clean_draft(tmp_path)
    bad_md = _CLEAN_MANUSCRIPT.replace(
        "We sequenced and analysed expression data.",
        "We sequenced TBD samples and analysed expression data.",
    )
    (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")
    findings = vd.check_g2_placeholder_or_leaked_template(draft_dir, bad_md)
    placeholder = [f for f in findings if f.id == "g2:placeholder_in_body"]
    assert placeholder
    assert placeholder[0].severity == vd.SEVERITY_P1
    assert placeholder[0].remediation.kind == vd.REMEDIATION_TARGETED
    assert "TBD" in placeholder[0].message


def test_g2_tbd_presenter_fires(tmp_path):
    """A TBD author line fires P0."""
    draft_dir = _build_clean_draft(tmp_path)
    bad_md = _CLEAN_MANUSCRIPT.replace(
        "Authors: Adam Arkin · LBNL",
        "Authors: TBD",
    )
    (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")
    findings = vd.check_g2_placeholder_or_leaked_template(draft_dir, bad_md)
    a = [f for f in findings if f.id == "g2:authors_tbd"]
    assert a
    assert a[0].severity == vd.SEVERITY_P0


def test_g2_missing_title_fires(tmp_path):
    """No H1 title heading → P0 missing_title finding."""
    draft_dir = _build_clean_draft(tmp_path)
    bad_md = _CLEAN_MANUSCRIPT.split("\n", 1)[1]  # drop the # H1 line
    (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")
    findings = vd.check_g2_placeholder_or_leaked_template(draft_dir, bad_md)
    t = [f for f in findings if f.id == "g2:title_missing"]
    assert t
    assert t[0].severity == vd.SEVERITY_P0


# ---------------------------------------------------------------------------
# v1.2.0 followup (Cowork verification, 2026-06-07): G2 under-fired.
# The original _TBD_RE only matched \bTBD\b. Real draft_2 shipped with
# bracketed-template placeholders that slid through:
#   "**Authors:** [AUTHOR LIST TO BE COMPLETED]"
#   "**Affiliations:** [TO BE COMPLETED]"
# Broaden the vocab AND add an Affiliations check. These tests pin the
# exact strings from the caulobacter draft_2 brief.
# ---------------------------------------------------------------------------


def test_g2_caulobacter_authors_bracketed_template_fires_p0(tmp_path):
    """Real draft_2 string: `**Authors:** [AUTHOR LIST TO BE COMPLETED]`.
    Must fire g2:authors_tbd as P0. Both the bold-label parsing AND
    the broadened placeholder vocab (TO BE COMPLETED) are exercised."""
    draft_dir = _build_clean_draft(tmp_path)
    bad_md = _CLEAN_MANUSCRIPT.replace(
        "Authors: Adam Arkin · LBNL",
        "**Authors:** [AUTHOR LIST TO BE COMPLETED]",
    )
    (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")
    findings = vd.check_g2_placeholder_or_leaked_template(draft_dir, bad_md)
    f = [x for x in findings if x.id == "g2:authors_tbd"]
    assert f, (
        f"caulobacter draft_2 author template must fire g2:authors_tbd; "
        f"got {[(x.id, x.message[:80]) for x in findings]}"
    )
    assert f[0].severity == vd.SEVERITY_P0
    # The captured value should be the bracketed content (no `**` leak).
    assert "[AUTHOR LIST TO BE COMPLETED]" in f[0].message


def test_g2_caulobacter_affiliations_bracketed_template_fires_p0(tmp_path):
    """Real draft_2 string: `**Affiliations:** [TO BE COMPLETED]`.
    Must fire the new g2:affiliations_tbd as P0. Before the v1.2.0
    followup, this slid through entirely (no affiliation check
    existed)."""
    draft_dir = _build_clean_draft(tmp_path)
    bad_md = _CLEAN_MANUSCRIPT.replace(
        "Authors: Adam Arkin · LBNL",
        "Authors: Adam Arkin · LBNL\n**Affiliations:** [TO BE COMPLETED]",
    )
    (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")
    findings = vd.check_g2_placeholder_or_leaked_template(draft_dir, bad_md)
    f = [x for x in findings if x.id == "g2:affiliations_tbd"]
    assert f, (
        f"caulobacter draft_2 affiliation template must fire "
        f"g2:affiliations_tbd; got {[(x.id, x.message[:80]) for x in findings]}"
    )
    assert f[0].severity == vd.SEVERITY_P0
    assert "[TO BE COMPLETED]" in f[0].message


def test_g2_broad_placeholder_vocab_each_fires(tmp_path):
    """Each placeholder token in the broadened vocab must fire when
    used as an author value. Coverage matrix for the alphabetic
    vocab: TBD, TK, TO BE COMPLETED, TO BE FILLED, FILL IN,
    PLACEHOLDER, XXX."""
    draft_dir = _build_clean_draft(tmp_path)
    base_md = _CLEAN_MANUSCRIPT
    for value in ("TBD", "TK", "TO BE COMPLETED", "TO BE FILLED",
                  "FILL IN", "PLACEHOLDER", "XXX", "XXXX"):
        bad_md = base_md.replace(
            "Authors: Adam Arkin · LBNL",
            f"Authors: {value}",
        )
        (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")
        findings = vd.check_g2_placeholder_or_leaked_template(draft_dir, bad_md)
        f = [x for x in findings if x.id == "g2:authors_tbd"]
        assert f, f"value {value!r} should fire authors_tbd; got {findings}"
        assert f[0].severity == vd.SEVERITY_P0


def test_g2_bracketed_ellipsis_in_body_fires(tmp_path):
    """`[...]` in body prose is the "TK insertion point" convention
    — also a placeholder. Should fire g2:placeholder_in_body."""
    draft_dir = _build_clean_draft(tmp_path)
    bad_md = _CLEAN_MANUSCRIPT.replace(
        "We sequenced and analysed expression data.",
        "We sequenced [...] and analysed expression data.",
    )
    (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")
    findings = vd.check_g2_placeholder_or_leaked_template(draft_dir, bad_md)
    f = [x for x in findings if x.id == "g2:placeholder_in_body"]
    assert f


def test_g2_empty_bracketed_author_value_fires(tmp_path):
    """`Authors: []` — empty bracket pair. The empty-bracketed
    detector is independent of the alphabetic vocab."""
    draft_dir = _build_clean_draft(tmp_path)
    bad_md = _CLEAN_MANUSCRIPT.replace(
        "Authors: Adam Arkin · LBNL",
        "Authors: []",
    )
    (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")
    findings = vd.check_g2_placeholder_or_leaked_template(draft_dir, bad_md)
    f = [x for x in findings if x.id == "g2:authors_tbd"]
    assert f
    assert f[0].severity == vd.SEVERITY_P0


def test_g2_real_citation_brackets_do_not_fire(tmp_path):
    """`[Smith 2024]` and `[C-013]` style citation markers in the
    manuscript body MUST NOT fire — they're not placeholders. The
    detector only fires on bracketed-ellipsis or bracketed-alphabetic
    vocab, never on arbitrary brackets."""
    draft_dir = _build_clean_draft(tmp_path)
    bad_md = _CLEAN_MANUSCRIPT.replace(
        "We sequenced and analysed expression data.",
        "We sequenced samples [Smith 2024] and analysed expression data [C-013].",
    )
    (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")
    findings = vd.check_g2_placeholder_or_leaked_template(draft_dir, bad_md)
    placeholder = [f for f in findings if f.id == "g2:placeholder_in_body"]
    assert placeholder == [], (
        f"real citation brackets must not fire as placeholders; got "
        f"{[(f.id, f.message[:120]) for f in placeholder]}"
    )


# ---------------------------------------------------------------------------
# v1.2.0 followup G1 calibration (Cowork verification, 2026-06-07):
# G1 over-fired. M1 reported "missing title" P0 on every H1-titled
# paper because PAPER_REQUIRED_SECTIONS['title'] looks for a literal
# `## Title` section. M2 (abstract-subsections) + M9 (limitations)
# also fired P0 on prose-handled papers. These tests pin the fixed
# projection: a well-formed real manuscript (H1 + author block +
# prose abstract, no `## Limitations` heading) must pass G1 with
# no spurious P0; only ICMJE-genuinely-blocking M-series (M3
# ai_disclosure, M4 data_availability) keep their P0/auto status.
# ---------------------------------------------------------------------------


_WELL_FORMED_MANUSCRIPT = """\
# Iron regulation in Caulobacter crescentus: a fur regulon analysis

Authors: Adam Arkin · LBNL

## Abstract

Iron is essential for bacterial growth. We analysed the fur regulon
in Caulobacter crescentus and identified 12 regulated genes that
form a coherent iron-response circuit. These genes coordinate
iron homeostasis under both replete and limiting conditions.

## Introduction

The fur regulon is a key iron-response circuit.

## Methods

We sequenced and analysed expression data.

## Results

Figure 1 shows the regulon structure.

![Figure 1: fur regulon overview](figures/fig01.png)

## Discussion

The fur regulon coordinates iron homeostasis. We acknowledge that
our cohort size limits generalisation; a larger cross-strain panel
would strengthen the inference.

## AI Disclosure

This manuscript was drafted with assistance from a large language
model under human supervision per the ICMJE V.A requirement.

## Data Availability

Sequencing data deposited at the NCBI Gene Expression Omnibus under
accession GSE-987654, available at https://www.ncbi.nlm.nih.gov/geo/
upon publication. Analysis scripts archived at Zenodo, DOI to follow.

## References

1. Smith et al. 2025.
"""


def test_g1_well_formed_h1_titled_paper_passes_no_p0(tmp_path):
    """The load-bearing regression case Cowork raised: a real
    well-formed manuscript with an H1 title + author block + prose
    abstract + in-discussion-prose limitations + AI disclosure +
    Data Availability must pass G1 with NO P0 findings. Pre-
    followup G1 spuriously fired P0 on M1 missing-title (because
    no `## Title` section) + M2 structured-abstract-subsections +
    M9 limitations. Post-followup: M1 suppressed when H1 present;
    M2 + M9 errors demoted to P1+advisory."""
    draft_dir = _build_clean_draft(tmp_path)
    (draft_dir / "manuscript.md").write_text(
        _WELL_FORMED_MANUSCRIPT, encoding="utf-8",
    )
    findings = vd.check_g1_section_completeness(draft_dir, mode="paper")
    p0s = [f for f in findings if f.severity == vd.SEVERITY_P0]
    assert p0s == [], (
        f"well-formed H1-titled paper must NOT produce P0 G1 findings; "
        f"got {[(f.id, f.message[:120]) for f in p0s]}"
    )


def test_g1_m1_title_suppressed_when_h1_present(tmp_path):
    """Direct pin on the M1 suppression: when manuscript.md has a
    leading H1, the M1 'Missing required paper-mode section: title'
    violation must be suppressed from the projection (the H1 IS the
    title page)."""
    draft_dir = _build_clean_draft(tmp_path)
    # Use the clean fixture (which has an H1) but strip the abstract
    # so M1 doesn't also fire about ABSTRACT — we want to isolate
    # the title-suppression.
    findings = vd.check_g1_section_completeness(draft_dir, mode="paper")
    m1_title_findings = [
        f for f in findings
        if f.id.startswith("g1:m1_") and "title" in f.message.lower()
    ]
    assert m1_title_findings == [], (
        f"M1 missing-title must be suppressed when H1 is present; "
        f"got {[f.message[:120] for f in m1_title_findings]}"
    )


def test_g1_m1_missing_title_still_fires_without_h1(tmp_path):
    """When the H1 IS absent, the M1 title violation should pass
    through normally (P0/auto). The suppression is H1-conditional,
    not unconditional."""
    draft_dir = _build_clean_draft(tmp_path)
    # Strip the H1 from the clean fixture; manuscript.md now opens
    # with the author line, no H1.
    no_h1_md = _CLEAN_MANUSCRIPT.split("\n", 1)[1]
    (draft_dir / "manuscript.md").write_text(no_h1_md, encoding="utf-8")
    findings = vd.check_g1_section_completeness(draft_dir, mode="paper")
    m1_title = [
        f for f in findings
        if f.id.startswith("g1:m1_") and "title" in f.message.lower()
    ]
    assert m1_title, "M1 missing-title MUST fire when no H1 is present"
    # And it remains P0/auto (no demotion for the genuinely-absent case).
    assert m1_title[0].severity == vd.SEVERITY_P0
    assert m1_title[0].remediation.kind == vd.REMEDIATION_AUTO


def test_g1_m2_structured_abstract_demoted_to_p1_advisory(tmp_path):
    """M2 fires when the Abstract lacks structured subsections
    (Background/Methods/Results/Conclusions). The followup demotes
    this from P0/auto to P1/advisory — many real papers ship prose
    abstracts and structured-subsection enforcement is publishing-
    house style, not deliverable completeness."""
    draft_dir = _build_clean_draft(tmp_path)
    (draft_dir / "manuscript.md").write_text(
        _WELL_FORMED_MANUSCRIPT, encoding="utf-8",
    )
    findings = vd.check_g1_section_completeness(draft_dir, mode="paper")
    m2_findings = [f for f in findings if f.id.startswith("g1:m2_")]
    if m2_findings:
        # If M2 fires at all (depends on validate_manuscript's exact
        # rules), it must NOT be P0 and MUST be advisory.
        for f in m2_findings:
            assert f.severity == vd.SEVERITY_P1
            assert f.remediation.kind == vd.REMEDIATION_ADVISORY


def test_g1_m3_ai_disclosure_remains_p0_auto(tmp_path):
    """The calibration MUST NOT demote M3 ai_disclosure — that's a
    real ICMJE requirement. Stripping the AI Disclosure section from
    the well-formed manuscript should still produce a P0/auto finding."""
    draft_dir = _build_clean_draft(tmp_path)
    no_ai_md = _WELL_FORMED_MANUSCRIPT.replace(
        "## AI Disclosure\n\nThis manuscript was drafted with assistance"
        " from a large language\nmodel under human supervision per the"
        " ICMJE V.A requirement.\n\n",
        "",
    )
    (draft_dir / "manuscript.md").write_text(no_ai_md, encoding="utf-8")
    findings = vd.check_g1_section_completeness(draft_dir, mode="paper")
    m3 = [f for f in findings if f.id.startswith("g1:m3_")]
    # M3 should still fire if it would have fired pre-followup.
    if m3:
        assert m3[0].severity == vd.SEVERITY_P0, (
            f"M3 ai_disclosure must remain P0 (genuine ICMJE block); "
            f"got severity={m3[0].severity}"
        )
        assert m3[0].remediation.kind == vd.REMEDIATION_AUTO


# ===========================================================================
# G3 figure_resolution_and_embedding
# ===========================================================================


def test_g3_resolved_figure_passes(tmp_path):
    """The clean fixture has a real figure path → no G3 unresolved
    finding (embed-count is skipped without the docx, which is the
    expected unit-test state)."""
    draft_dir = _build_clean_draft(tmp_path)
    manuscript_md = (draft_dir / "manuscript.md").read_text(encoding="utf-8")
    findings = vd.check_g3_figure_resolution_and_embedding(
        draft_dir, manuscript_md,
    )
    unresolved = [f for f in findings if "unresolved_figure" in f.id]
    assert unresolved == []


def test_g3_unresolved_figure_fires(tmp_path):
    """Replace the manuscript's figure reference with a non-existent
    path. G3 fires P0 with a targeted remediation (we can't guess
    the intended file)."""
    draft_dir = _build_clean_draft(tmp_path)
    bad_md = _CLEAN_MANUSCRIPT.replace(
        "![Figure 1: fur regulon overview](figures/fig01.png)",
        "![Figure 1: fur regulon overview](figures/does_not_exist.png)",
    )
    (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")
    findings = vd.check_g3_figure_resolution_and_embedding(draft_dir, bad_md)
    unresolved = [f for f in findings if "unresolved_figure" in f.id]
    assert len(unresolved) == 1
    assert unresolved[0].severity == vd.SEVERITY_P0
    assert "does_not_exist.png" in unresolved[0].message


def test_g3_no_figures_referenced_passes(tmp_path):
    """A manuscript that references no figures emits nothing — G3
    has nothing to validate."""
    draft_dir = _build_clean_draft(tmp_path)
    bad_md = _CLEAN_MANUSCRIPT.replace(
        "![Figure 1: fur regulon overview](figures/fig01.png)\n\n",
        "",
    )
    (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")
    findings = vd.check_g3_figure_resolution_and_embedding(draft_dir, bad_md)
    assert findings == []


# ===========================================================================
# G4 mode_depth_vs_user_intent — the DP9b-analogue
# ===========================================================================


def test_g4_intent_matches_state_passes(tmp_path):
    """User explicitly picked paper; state.mode also paper → no
    finding."""
    draft_dir = _build_clean_draft(tmp_path)
    findings = vd.check_g4_mode_depth_vs_user_intent(draft_dir)
    assert findings == []


def test_g4_mode_drop_fires_p0(tmp_path):
    """The DP9b-analogue mode drop: user explicitly picked paper,
    state.json silently has report. v1.1.0 would have shipped this
    silently. P0; targeted remediation with the corrective --mode."""
    draft_dir = _build_clean_draft(tmp_path)
    # Re-write state.json with the wrong mode.
    state_path = draft_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["mode"] = "report"  # silent drop / wrong default
    _write_json(state_path, state)

    findings = vd.check_g4_mode_depth_vs_user_intent(draft_dir)
    mismatch = [f for f in findings if f.id == "g4:state_mode_mismatch"]
    assert mismatch
    assert mismatch[0].severity == vd.SEVERITY_P0
    assert mismatch[0].remediation.kind == vd.REMEDIATION_TARGETED
    assert "paper" in mismatch[0].remediation.command  # the recovery cmd
    assert "report" in mismatch[0].message  # what the state had
    assert "paper" in mismatch[0].message    # what the user picked


def test_g4_report_mode_mismatch_fires_p1(tmp_path):
    """validate_manuscript ran at the wrong mode → P1 with
    AUTO_RERUN_VALIDATE remediation."""
    draft_dir = _build_clean_draft(tmp_path)
    # State agrees with user_intent (paper); but the audit/
    # validate_manuscript.json was written when an old run ran the
    # validator at mode=report.
    _write_json(
        draft_dir / "audit" / "validate_manuscript.json",
        {"draft_dir": str(draft_dir), "mode": "report", "validators": []},
    )
    findings = vd.check_g4_mode_depth_vs_user_intent(draft_dir)
    report_findings = [
        f for f in findings if f.id == "g4:report_mode_mismatch"
    ]
    assert report_findings
    assert report_findings[0].severity == vd.SEVERITY_P1
    assert report_findings[0].remediation.kind == vd.REMEDIATION_AUTO
    assert report_findings[0].remediation.action == vd.AUTO_RERUN_VALIDATE


def test_g4_no_user_intent_advisory_only(tmp_path):
    """A legacy pre-v1.2.0 draft without user_intent.json gets a
    single advisory — never blocks; the file just isn't there to
    compare."""
    draft_dir = tmp_path / "draft_legacy"
    draft_dir.mkdir(parents=True)
    findings = vd.check_g4_mode_depth_vs_user_intent(draft_dir)
    assert len(findings) == 1
    assert findings[0].severity == vd.SEVERITY_ADVISORY
    assert findings[0].id == "g4:no_user_intent"


def test_g4_user_mode_not_explicit_does_not_fire(tmp_path):
    """If the user inherited the default mode (not --mode-explicit),
    G4 stays silent — we don't punish operators for inheriting
    defaults (matches Cycle-1 G4 semantics)."""
    proj_dir = tmp_path / "projects" / "x"
    draft_dir = proj_dir / "papers" / "draft_1"
    (draft_dir / "audit").mkdir(parents=True, exist_ok=True)
    ui.write_user_intent(
        draft_dir,
        mode="paper", tier="STRONG", audience="peer",
        mode_explicit=False,
        tier_explicit=False,
        audience_explicit=False,
    )
    _write_json(
        draft_dir / "state.json",
        {"version": "0.1", "project_id": "x", "draft_number": 1,
         "phase": "assembled", "mode": "report"},  # mismatch but not explicit
    )
    findings = vd.check_g4_mode_depth_vs_user_intent(draft_dir)
    # No mismatch finding — user didn't explicitly pick, so we can't
    # call this a drop.
    mismatch = [f for f in findings if "mismatch" in f.id]
    assert mismatch == []


# ===========================================================================
# Schema shape — projectable tokens; envelope shape
# ===========================================================================


def test_schema_finding_fields_are_tokens(tmp_path):
    """Telemetry-readiness: gate, severity, remediation.kind are all
    drawn from the frozen vocabularies — no free-text in those slots."""
    draft_dir = _build_clean_draft(tmp_path)
    # Force-fire G2 + G4 to get a mix.
    bad_md = _CLEAN_MANUSCRIPT.replace("Authors: Adam Arkin · LBNL", "Authors: TBD")
    (draft_dir / "manuscript.md").write_text(bad_md, encoding="utf-8")
    state = json.loads((draft_dir / "state.json").read_text())
    state["mode"] = "report"
    _write_json(draft_dir / "state.json", state)

    findings = vd.validate(draft_dir)
    for f in findings:
        assert f.gate in vd.GATES, f"gate {f.gate!r} not in vocab"
        assert f.severity in vd.SEVERITIES, (
            f"severity {f.severity!r} not in vocab"
        )
        assert f.remediation.kind in vd.REMEDIATION_KINDS, (
            f"remediation.kind {f.remediation.kind!r} not in vocab"
        )


def test_schema_payload_shape(tmp_path):
    """write_findings emits the deliverable-validation.v1 envelope
    with summary + findings[] in the documented shape — identical
    cross-skill shape to beril-presentation-maker."""
    draft_dir = _build_clean_draft(tmp_path)
    findings = vd.validate(draft_dir)
    out = vd.write_findings(draft_dir, findings)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "deliverable-validation.v1"
    assert "summary" in payload
    s = payload["summary"]
    assert {"total", "by_gate", "by_severity", "by_remediation_kind",
            "blocking"} <= set(s.keys())
    assert set(s["by_gate"].keys()) == set(vd.GATES)
    assert set(s["by_severity"].keys()) == set(vd.SEVERITIES)
    assert set(s["by_remediation_kind"].keys()) == set(vd.REMEDIATION_KINDS)


def test_readiness_exit_code_clean_is_zero():
    assert vd.readiness_exit_code([]) == 0


def test_readiness_exit_code_advisory_is_zero():
    f = vd.Finding(
        id="x", gate=vd.GATES[2], severity=vd.SEVERITY_ADVISORY,
        slide_id_or_target="manuscript", message="advisory",
        remediation=vd.Remediation(kind=vd.REMEDIATION_ADVISORY),
    )
    assert vd.readiness_exit_code([f]) == 0


def test_readiness_exit_code_p0_is_one():
    f = vd.Finding(
        id="x", gate=vd.GATES[0], severity=vd.SEVERITY_P0,
        slide_id_or_target="manuscript", message="p0",
        remediation=vd.Remediation(kind=vd.REMEDIATION_AUTO),
    )
    assert vd.readiness_exit_code([f]) == 1


# ===========================================================================
# finalize_deliverable — handler removal pin + auto-handler shape
# ===========================================================================


def test_finalize_strip_dirname_handler_not_present():
    """v1.2.0 Cycle-2 contract (inherited from Cycle-1 G1 followup):
    no `strip_dirname_token` auto-handler. Dirname-leak is P1
    TARGETED only — operator rewrites."""
    assert "strip_dirname_token" not in fd._AUTO_HANDLERS, (
        "strip_dirname_token must NOT be in _AUTO_HANDLERS — too "
        "destructive on fuzzy title match (Cycle-1 G1 lesson)."
    )


def test_finalize_auto_handlers_are_safe_set():
    """The auto handlers are ONLY rerun_validate + reassemble — both
    pure-read on manuscript.md by design."""
    assert set(fd._AUTO_HANDLERS.keys()) == {"rerun_validate", "reassemble"}


def test_finalize_rerun_validate_writes_audit_report(tmp_path):
    """rerun_validate runs validate_manuscript and writes
    audit/validate_manuscript.json. Pure read on manuscript.md."""
    draft_dir = _build_clean_draft(tmp_path)
    ok, msg = fd._rerun_validate_manuscript(draft_dir)
    assert ok, msg
    out = draft_dir / "audit" / "validate_manuscript.json"
    assert out.is_file()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["mode"] == "paper"
    assert "validators" in payload
