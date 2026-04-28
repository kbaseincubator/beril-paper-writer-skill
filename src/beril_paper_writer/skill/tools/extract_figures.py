#!/usr/bin/env python3
"""extract_figures.py — figure inventory and caption candidates.

Per SPEC §6 + DECISIONS D-004: v1 reuses existing project figures only
(no regeneration). This script inventories every image file in the
project's figures directory and gathers caption candidates from three
sources:

  1. REPORT.md image references — `![alt text](figures/X.png)` lines.
     The alt text is the project's own authored caption; this is the
     STRONGEST source.

  2. Notebook savefig context — `plt.savefig(FIGS / 'X.png', ...)` calls
     in notebook code cells. The immediately-preceding markdown cell
     (or the last paragraph of one) is captured as a caption candidate;
     the notebook+cell is recorded as the figure's origin.

  3. Filename stems — `fig01_growth_curves.png` → "Growth curves".
     Always available as a fallback.

This script does NOT select which figures end up in the manuscript —
that's prompt-driven (the Plan agent or Methods agent, given a chosen
throughline, picks 4–8 from the inventory). Per SPEC §6, the writer
must REUSE existing figures; missing figures become explicit
gap-fill requests.

Standalone-script + importable-module pattern, mirroring
extract_methods.py and validate_manuscript.py.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Image-format inference
# ---------------------------------------------------------------------------

_IMAGE_EXTENSIONS = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".gif": "gif",
    ".svg": "svg",
    ".pdf": "pdf",
    ".webp": "webp",
    ".tif": "tiff",
    ".tiff": "tiff",
}


def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_EXTENSIONS


def infer_image_format(path: Path) -> str:
    return _IMAGE_EXTENSIONS.get(path.suffix.lower(), "unknown")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class CaptionCandidate:
    """One caption candidate for a figure, with its source."""

    source: str           # "report" | "notebook_md" | "filename"
    text: str
    context: dict = field(default_factory=dict)
    # context fields by source:
    #   report:      {"line": int, "section": Optional[str]}
    #   notebook_md: {"notebook": str, "preceding_cell": int}
    #   filename:    {} (no context needed)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class SavefigOrigin:
    """One savefig call that produced (or might have produced) a figure."""

    notebook: str         # relative path
    cell: int
    line: int
    raw_call: str         # snippet of the savefig call as written

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class PanelDescriptor:
    """One panel's structured metadata (v0.4 Phase 2 multi-panel awareness).

    Letter is row-major from `plt.subplots(N,M)` AST detection (A=axes[0,0],
    B=axes[0,1], etc.) OR from `(Fig. N[A-Z])` callouts in REPORT.md /
    Results-section prose (Phase 3 prose-detection pass).
    """

    letter: str
    title: Optional[str] = None
    xlabel: Optional[str] = None
    ylabel: Optional[str] = None
    prose_context: Optional[str] = None  # ±1 sentence from REPORT/Results

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class CaptionDescriptor:
    """Structured rich-caption descriptor (v0.4 Tier 8 / inventory_schema v2).

    Distinct from CaptionCandidate. Each FigureRecord carries at most one
    descriptor. Field-population schedule across the v0.4 ladder:

      Phase 1b: notebook_prose (from `_collect_md_walkback`); source_refs
                gets 'notebook_md_walkback'.
      Phase 2:  title, axes_labels, legend_labels, panels (from matplotlib
                AST extraction); source_refs gets 'matplotlib_ast'.
      Phase 3:  panels merged with prose-detected `(Fig. N[A-Z])` callouts;
                source_refs gets 'prose_panel_callout'.
      Phase 4:  on Source 4 invocation, the LLM-synthesized legend is
                stored separately (it consumes the descriptor as input,
                doesn't overwrite); see audit/figure_caption.v1.metadata.json.

    `notebook_prose` is the unredacted walk-back source (capped at
    `_DESCRIPTION_TEXT_CAP`); downstream consumers (resolve-figures,
    sufficiency gate) apply heading-strip + word-count transforms
    caller-side.
    """

    title: Optional[str] = None
    axes_labels: list[str] = field(default_factory=list)
    legend_labels: list[str] = field(default_factory=list)
    notebook_prose: Optional[str] = None
    panels: list[PanelDescriptor] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True if no Tier 8 signal has been populated."""
        return (
            self.title is None
            and not self.axes_labels
            and not self.legend_labels
            and self.notebook_prose is None
            and not self.panels
        )

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "axes_labels": list(self.axes_labels),
            "legend_labels": list(self.legend_labels),
            "notebook_prose": self.notebook_prose,
            "panels": [p.to_dict() for p in self.panels],
            "source_refs": list(self.source_refs),
        }


# v0.4 Phase 1b: cap for full walk-back text stored in
# CaptionDescriptor.notebook_prose. Empirical: max walk-back across
# functional_dark_matter is 3424 chars (p95=2782, median=121); 4000
# gives comfortable headroom while bounding pathological cases. This is
# a safety bound, not a typical-case truncator.
_DESCRIPTION_TEXT_CAP = 4000


@dataclass
class FigureRecord:
    """All metadata for one figure file."""

    path: str             # relative to project_dir
    filename: str
    size_bytes: int
    format: str           # "png" | "jpeg" | etc.
    captions: list[CaptionCandidate] = field(default_factory=list)
    savefig_origins: list[SavefigOrigin] = field(default_factory=list)
    # v0.4 Phase 1b: structured rich-caption descriptor (inventory schema v2).
    # Each figure has at most one descriptor; default is empty (all fields
    # None / empty list). is_empty() is the load-bearing predicate for
    # downstream renderers + the Phase 4c sufficiency gate.
    description: CaptionDescriptor = field(default_factory=CaptionDescriptor)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "format": self.format,
            "captions": [c.to_dict() for c in self.captions],
            "savefig_origins": [s.to_dict() for s in self.savefig_origins],
            "description": self.description.to_dict(),
        }


@dataclass
class FigureInventoryReport:
    """Top-level report from a figure-extraction run."""

    project_dir: str
    figures_dirs: list[str]   # relative paths actually scanned
    figures: list[FigureRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "project_dir": self.project_dir,
            "figures_dirs": self.figures_dirs,
            "figures": [f.to_dict() for f in self.figures],
        }
        d["summary"] = self._summary()
        return d

    def _summary(self) -> dict:
        from collections import Counter
        formats = Counter(f.format for f in self.figures)
        with_notebook = sum(1 for f in self.figures if f.savefig_origins)
        with_report = sum(
            1 for f in self.figures
            if any(c.source == "report" for c in f.captions)
        )
        with_filename_only = sum(
            1 for f in self.figures
            if not f.savefig_origins
            and not any(c.source == "report" for c in f.captions)
        )
        total_bytes = sum(f.size_bytes for f in self.figures)
        return {
            "total_figures": len(self.figures),
            "total_size_bytes": total_bytes,
            "by_format": dict(formats),
            "with_notebook_origin": with_notebook,
            "with_report_reference": with_report,
            "filename_only": with_filename_only,
        }


# ---------------------------------------------------------------------------
# Filename → caption heuristic
# ---------------------------------------------------------------------------

# Common figure-filename prefixes we strip to recover a sensible caption.
# Examples:
#   fig01_growth_curves.png   → "Growth curves"
#   01_carbon_util.png        → "Carbon util"
#   NB00_per_strain.png       → "Per strain"
#   figure_3_inhibition.png   → "Inhibition"
_FILENAME_PREFIX_RE = re.compile(
    r"^(?:fig(?:ure)?_?|nb_?|panel_?)?\d+[a-z]?[_\-]?",
    re.IGNORECASE,
)


def filename_to_caption(filename: str) -> str:
    """Convert a figure filename into a fallback caption."""
    stem = Path(filename).stem
    cleaned = _FILENAME_PREFIX_RE.sub("", stem)
    cleaned = cleaned.replace("_", " ").replace("-", " ").strip()
    if not cleaned:
        # Nothing after stripping the prefix; use the original stem.
        cleaned = stem.replace("_", " ").replace("-", " ").strip()
    # Capitalize first letter; keep rest as-is (preserves acronyms).
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


# ---------------------------------------------------------------------------
# REPORT.md image-reference parser
# ---------------------------------------------------------------------------

# Markdown image syntax: ![alt text](url)
# alt text may contain anything except a bare ']'; url is everything up to ')'.
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


@dataclass
class ReportImageRef:
    """One image reference found in REPORT.md."""

    line: int
    alt_text: str
    url: str
    section: Optional[str]  # nearest preceding H1/H2 header text


def parse_report_image_references(report_text: str) -> list[ReportImageRef]:
    """Extract every `![alt](url)` reference from REPORT.md, with context."""
    refs: list[ReportImageRef] = []
    current_section: Optional[str] = None
    for line_no, line in enumerate(report_text.split("\n"), start=1):
        # Update section context if this is a header.
        m_hdr = re.match(r"^#{1,2}\s+(.+?)\s*#*\s*$", line)
        if m_hdr:
            current_section = m_hdr.group(1).strip()
        for m in _MD_IMAGE_RE.finditer(line):
            refs.append(ReportImageRef(
                line=line_no,
                alt_text=m.group(1).strip(),
                url=m.group(2).strip(),
                section=current_section,
            ))
    return refs


# ---------------------------------------------------------------------------
# Notebook savefig walker
# ---------------------------------------------------------------------------

_MAGIC_RE = re.compile(r"^\s*[%!?]")


def _strip_jupyter_magics(source: str) -> str:
    """Replace IPython magics / shell calls with blank lines (preserves
    line numbers). Vendored from extract_methods.py to keep this script
    independent — same 10-line helper, no shared-module dance."""
    out_lines: list[str] = []
    for line in source.split("\n"):
        out_lines.append("" if _MAGIC_RE.match(line) else line)
    return "\n".join(out_lines)


def _last_string_in_path_expr(node: ast.AST) -> Optional[str]:
    """Walk a path-construction expression and return the rightmost
    string literal. Handles common patterns:

      'foo.png'                        → 'foo.png'
      FIGS / 'foo.png'                 → 'foo.png'
      FIGS / 'sub' / 'foo.png'         → 'foo.png'
      Path(...) / 'foo.png'            → 'foo.png'
      os.path.join(FIGS, 'foo.png')    → 'foo.png'
      str(FIGS / 'foo.png')            → 'foo.png'
      f'foo.png'                       → 'foo.png'  (constant joined-str)
    """
    # Simple string literal
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    # f-string with no interpolation — single Constant inside JoinedStr
    if isinstance(node, ast.JoinedStr):
        if len(node.values) == 1 and isinstance(node.values[0], ast.Constant):
            return node.values[0].value
        # Joined-str with interpolation: try to recover the trailing literal
        # if the last value is a Constant.
        if node.values and isinstance(node.values[-1], ast.Constant):
            tail = node.values[-1].value
            if isinstance(tail, str) and "." in tail:
                return tail
        return None
    # BinOp with `/`: take the right side
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        right = _last_string_in_path_expr(node.right)
        if right:
            return right
        # Fall through to the left side just in case
        return _last_string_in_path_expr(node.left)
    # Call: os.path.join, Path(...), str(...)
    if isinstance(node, ast.Call):
        # Check the function being called
        func_path = []
        f = node.func
        while isinstance(f, ast.Attribute):
            func_path.insert(0, f.attr)
            f = f.value
        if isinstance(f, ast.Name):
            func_path.insert(0, f.id)
        full = ".".join(func_path)
        if full in ("os.path.join", "Path", "pathlib.Path", "str"):
            # Look at the last positional arg
            if node.args:
                return _last_string_in_path_expr(node.args[-1])
    return None


def _is_savefig_call(node: ast.Call) -> bool:
    """True if this Call looks like a `*.savefig(...)` call.

    Catches `plt.savefig`, `fig.savefig`, `ax.figure.savefig`,
    `pyplot.savefig`, etc. — any Attribute call where the trailing
    attribute name is 'savefig'.
    """
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "savefig"
    )


# ---------------------------------------------------------------------------
# v0.4 Phase 2 — matplotlib AST extraction (Source 3)
#
# Hard scope cap: AST walks only the savefig cell + the immediately
# preceding code cell. Does NOT chase function calls into other modules.
# Only string-literal arguments are extracted; f-strings with single-
# Constant body are OK; interpolated f-strings return no signal (don't
# fabricate). Handles 1D `plt.subplots(N, M)` grid declarations and
# `plt.subplot(N, M, k)` index calls; does NOT handle gridspec.GridSpec
# or plt.subplot2grid (deferred to v0.5).
# ---------------------------------------------------------------------------


def _string_literal(node: ast.AST) -> Optional[str]:
    """Return the string value of a string-literal AST node.

    Constant strings → the value.
    f-strings with a single Constant body (e.g. f"foo") → the value.
    f-strings with interpolation (e.g. f"foo {x}") → None (don't fabricate).
    Anything else → None.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        if len(node.values) == 1 and isinstance(node.values[0], ast.Constant):
            v = node.values[0].value
            return v if isinstance(v, str) else None
    return None


def _extract_subscript_indices(node: ast.AST) -> Optional[tuple[int, ...]]:
    """If `node` is a Subscript with constant integer indices, return them.

    `axes[0]`     → (0,)
    `axes[0, 1]`  → (0, 1)
    `axes[i]`     → None (non-constant index; can't fabricate)
    `axes[0][1]`  → None (chained subscript; rare in matplotlib idiom)
    """
    if not isinstance(node, ast.Subscript):
        return None
    sl = node.slice
    if isinstance(sl, ast.Constant) and isinstance(sl.value, int):
        return (sl.value,)
    if isinstance(sl, ast.Tuple):
        idxs: list[int] = []
        for el in sl.elts:
            if isinstance(el, ast.Constant) and isinstance(el.value, int):
                idxs.append(el.value)
            else:
                return None
        return tuple(idxs)
    return None


def _idxs_to_letter(
    idxs: tuple[int, ...], grid_cols: Optional[int]
) -> Optional[str]:
    """Map subscript indices to a row-major panel letter.

    Single index → A=0, B=1, ... (axes is a 1D array).
    Two indices  → A=axes[0,0], B=axes[0,1], ... (row-major; needs grid_cols).
    Without grid_cols on a 2D access we cannot honestly assign letters
    (no fabrication).
    """
    if len(idxs) == 1:
        i = idxs[0]
        if 0 <= i < 26:
            return chr(ord("A") + i)
        return None
    if len(idxs) == 2:
        if grid_cols is None or grid_cols <= 0:
            return None
        i, j = idxs
        if i < 0 or j < 0 or j >= grid_cols:
            return None
        flat = i * grid_cols + j
        if flat < 26:
            return chr(ord("A") + flat)
        return None
    return None


def _classify_plot_call(node: ast.Call) -> tuple[Optional[str], object]:
    """Classify a Call node as a matplotlib plot operation.

    Returns (kind, info) tuple. Kinds:

      "title"          info = str (the title text)
      "suptitle"       info = str (suptitle text; used as title fallback)
      "axes_label"     info = str (xlabel, ylabel, or colorbar label)
      "legend"         info = list[str] of label strings (or [] if non-list arg)
      "subplots_grid"  info = {"cols": int|None}  — `plt.subplots(N, M)`
      "subplot_pos"    info = {"position": int}   — `plt.subplot(N, M, k)`
      "panel_set"      info = (field, indices, value)  — axes[i,j].set_*(...)
      None             — call is not a recognized plot operation

    Only attribute-style calls are classified (`plt.title`, `ax.set_title`,
    `axes[i,j].set_title`). Direct function calls like `volcano_plot(df)`
    are NOT classified — by design (no chasing into wrapper bodies).
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None, None
    attr = func.attr

    # axes[i,j].set_title / .set_xlabel / .set_ylabel — panel-level
    if attr in ("set_title", "set_xlabel", "set_ylabel"):
        indices = _extract_subscript_indices(func.value)
        value = _string_literal(node.args[0]) if node.args else None
        if indices is not None and value is not None:
            field = attr[len("set_"):]   # 'set_title' → 'title'
            return "panel_set", (field, indices, value)
        # ax.set_title (no subscript) — figure-level.
        if value is None:
            return None, None
        if attr == "set_title":
            return "title", value
        return "axes_label", value

    # Plain pyplot-style: plt.title / plt.xlabel / plt.ylabel / plt.legend
    if attr == "title":
        v = _string_literal(node.args[0]) if node.args else None
        return ("title", v) if v else (None, None)
    if attr == "suptitle":
        v = _string_literal(node.args[0]) if node.args else None
        return ("suptitle", v) if v else (None, None)
    if attr in ("xlabel", "ylabel"):
        v = _string_literal(node.args[0]) if node.args else None
        return ("axes_label", v) if v else (None, None)
    if attr == "set_label":
        # cbar.set_label('Z')
        v = _string_literal(node.args[0]) if node.args else None
        return ("axes_label", v) if v else (None, None)
    if attr == "legend":
        # legend(['a', 'b']) or legend(handles=..., labels=['a','b'])
        if node.args:
            first = node.args[0]
            if isinstance(first, (ast.List, ast.Tuple)):
                labels: list[str] = []
                for el in first.elts:
                    s = _string_literal(el)
                    if s:
                        labels.append(s)
                if labels:
                    return "legend", labels
        # Also check labels= kwarg
        for kw in node.keywords:
            if kw.arg == "labels" and isinstance(kw.value, (ast.List, ast.Tuple)):
                labels = []
                for el in kw.value.elts:
                    s = _string_literal(el)
                    if s:
                        labels.append(s)
                if labels:
                    return "legend", labels
        return None, None
    if attr == "subplots":
        cols: Optional[int] = None
        # Positional: plt.subplots(N, M, ...)
        if len(node.args) >= 2:
            second = node.args[1]
            if isinstance(second, ast.Constant) and isinstance(second.value, int):
                cols = second.value
        # Keyword: plt.subplots(nrows=N, ncols=M)
        for kw in node.keywords:
            if kw.arg == "ncols":
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, int):
                    cols = kw.value.value
        return "subplots_grid", {"cols": cols}
    if attr == "figure":
        # plt.figure() — new figure boundary; resets per-figure state in
        # _extract_plot_calls. (Includes the kwarg-only variant
        # plt.figure(figsize=(8,6)).)
        return "figure_call", None
    if attr == "subplot":
        # plt.subplot(N, M, k) — k is 1-based; activates one panel of an
        # existing figure. NOT a new-figure boundary (does not reset state).
        if len(node.args) >= 3:
            third = node.args[2]
            if isinstance(third, ast.Constant) and isinstance(third.value, int):
                return "subplot_pos", {"position": third.value}
        return None, None
    return None, None


@dataclass
class PlotCallExtraction:
    """AST-extracted matplotlib state at a savefig point.

    Phase 2 output. Captures string-literal arguments from title / axes /
    legend calls in the savefig cell + 1 preceding code cell, partitioned
    at savefig boundaries within the savefig cell. First-occurrence-wins
    semantics for scalar fields (title, axis labels); panels list dedupes
    by letter and merges sub-fields.
    """

    title: Optional[str] = None
    axes_labels: list[str] = field(default_factory=list)
    legend_labels: list[str] = field(default_factory=list)
    panels: list[PanelDescriptor] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            self.title is None
            and not self.axes_labels
            and not self.legend_labels
            and not self.panels
        )


def _extract_plot_calls(
    setup_cell_source: Optional[str],
    savefig_cell_source: str,
    savefig_line: int,
    prev_savefig_line: Optional[int] = None,
) -> PlotCallExtraction:
    """Extract matplotlib state for ONE savefig call.

    Scope:
      - `setup_cell_source` (the immediately preceding code cell) — entirely.
      - `savefig_cell_source` between `prev_savefig_line` (exclusive) and
        `savefig_line` (inclusive). For the FIRST savefig in the cell,
        `prev_savefig_line` is None and the scope starts at line 1.

    This partitioning supports the multi-savefig-per-cell idiom:

        plt.figure(); plt.title('A'); plt.savefig('a.png')   ← scope 1
        plt.figure(); plt.title('B'); plt.savefig('b.png')   ← scope 2

    where each savefig sees only its own setup, not the other's.

    Return value: PlotCallExtraction. is_empty() iff no signal recovered.
    """
    extraction = PlotCallExtraction()
    grid_cols: Optional[int] = None
    current_subplot_position: Optional[int] = None

    # Walk a source's AST, dispatching every matched call into the
    # extraction in source-line order.
    def _walk(source: Optional[str], line_lo: Optional[int],
              line_hi: Optional[int]) -> None:
        nonlocal grid_cols, current_subplot_position
        if not source or not source.strip():
            return
        cleaned = _strip_jupyter_magics(source)
        try:
            tree = ast.parse(cleaned)
        except SyntaxError:
            return

        # Flatten Calls in source-line order. ast.walk yields in BFS order
        # but we need source-text order to keep "current subplot" tracking
        # correct when a single cell has multiple subplot()+title() pairs.
        calls: list[ast.Call] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if line_lo is not None and node.lineno <= line_lo:
                    continue
                if line_hi is not None and node.lineno > line_hi:
                    continue
                calls.append(node)
        calls.sort(key=lambda n: (n.lineno, n.col_offset))

        for call in calls:
            kind, info = _classify_plot_call(call)
            if kind == "figure_call":
                # plt.figure() — new figure boundary. Wipe any state
                # accumulated from prior figures (typically the setup
                # cell's calls for a previous figure that's already been
                # saved). This is the load-bearing fix for the cross-cell
                # over-attribution bug discovered in v0.4 Phase 2 smoke
                # against functional_dark_matter (fig01/fig02 had
                # identical panel titles when each cell drew its own
                # multi-panel figure but the setup_cell_source carried
                # the prior figure's panel titles).
                extraction.title = None
                extraction.axes_labels.clear()
                extraction.legend_labels.clear()
                extraction.panels.clear()
                grid_cols = None
                current_subplot_position = None
            elif kind == "subplots_grid":
                cols = info.get("cols") if isinstance(info, dict) else None
                # Same boundary semantics: subplots() creates a new figure.
                extraction.title = None
                extraction.axes_labels.clear()
                extraction.legend_labels.clear()
                extraction.panels.clear()
                grid_cols = cols
                current_subplot_position = None
            elif kind == "subplot_pos":
                pos = info.get("position") if isinstance(info, dict) else None
                if isinstance(pos, int):
                    current_subplot_position = pos
            elif kind == "title":
                if extraction.title is None and isinstance(info, str):
                    # If we're inside a subplot context, attribute as panel.
                    if current_subplot_position is not None:
                        _attach_panel(extraction,
                                      ("title", (current_subplot_position - 1,), info),
                                      grid_cols=None)
                    else:
                        extraction.title = info
            elif kind == "suptitle":
                # suptitle is fig-level; doesn't go to a panel.
                if extraction.title is None and isinstance(info, str):
                    extraction.title = info
            elif kind == "axes_label" and isinstance(info, str):
                if current_subplot_position is not None:
                    _attach_panel(extraction,
                                  ("axes_label", (current_subplot_position - 1,), info),
                                  grid_cols=None)
                else:
                    if info not in extraction.axes_labels:
                        extraction.axes_labels.append(info)
            elif kind == "legend" and isinstance(info, list):
                for lab in info:
                    if lab not in extraction.legend_labels:
                        extraction.legend_labels.append(lab)
            elif kind == "panel_set":
                _attach_panel(extraction, info, grid_cols=grid_cols)

    _walk(setup_cell_source, None, None)
    _walk(savefig_cell_source, prev_savefig_line, savefig_line)

    # Sort panels by letter (A, B, C, ...) for stable rendering.
    extraction.panels.sort(key=lambda p: p.letter)
    return extraction


def _attach_panel(
    extraction: PlotCallExtraction,
    info: tuple,
    grid_cols: Optional[int],
) -> None:
    """Merge a panel-level field into the extraction's panels list.

    `info` is the tuple returned by _classify_plot_call for kind
    'panel_set' OR a synthesized tuple for plt.title-after-subplot
    contexts: (field, indices, value).

    Field-level first-occurrence-wins per panel.
    """
    field_name, indices, value = info
    letter = _idxs_to_letter(indices, grid_cols)
    if letter is None:
        # Subplot-position context: indices are (k-1,); single-axis idiom
        # means axes is conceptually 1D. Re-attempt with grid_cols=None
        # (which forces the single-index path in _idxs_to_letter).
        if len(indices) == 1:
            i = indices[0]
            if 0 <= i < 26:
                letter = chr(ord("A") + i)
        if letter is None:
            return
    panel = next((p for p in extraction.panels if p.letter == letter), None)
    if panel is None:
        panel = PanelDescriptor(letter=letter)
        extraction.panels.append(panel)
    if field_name == "title" and panel.title is None:
        panel.title = value
    elif field_name == "axes_label":
        # Best-effort: attribute to xlabel if empty, else ylabel.
        # axes[i,j].set_xlabel vs set_ylabel are distinguished upstream.
        if panel.xlabel is None:
            panel.xlabel = value
        elif panel.ylabel is None and value != panel.xlabel:
            panel.ylabel = value
    elif field_name == "xlabel" and panel.xlabel is None:
        panel.xlabel = value
    elif field_name == "ylabel" and panel.ylabel is None:
        panel.ylabel = value


@dataclass
class SavefigCall:
    """One savefig call discovered in a notebook cell."""

    notebook: str
    cell: int
    line: int
    saved_basename: Optional[str]   # extracted figure filename, if recoverable
    raw_call: str
    preceding_md_cell_index: Optional[int]
    # v0.4 Phase 2: matplotlib AST extraction (Source 3). None if no
    # plot calls could be classified or if the cell can't be AST-parsed.
    plot_calls: Optional[PlotCallExtraction] = None


def _cell_text(cell) -> str:
    """Extract a cell's source as a string (cells may store source as
    str or list[str] depending on nbformat version)."""
    src = cell.source
    return src if isinstance(src, str) else "".join(src)


def _collect_md_walkback(cells: list, savefig_raw_idx: int) -> Optional[str]:
    """Walk backward from a savefig cell, collecting markdown cells until
    a section break or the start of the notebook.

    A section break = markdown cell whose first non-blank line begins with
    `#` (any heading level). The header cell IS included in the walk-back
    (heading text is descriptive, e.g. "## Fig 1: Fitness landscape across pH").

    Replaces the v0.3 "consume one md cell, attribute to first following
    code cell" model, which silently failed on the dominant scientific-
    notebook idiom:

        [md]   "## Fig 1: Fitness landscape"
        [code] # data prep (no savefig)
        [code] plt.plot(...); plt.savefig(...)   ← saw nothing under v0.3

    Multiple savefigs in the same section legitimately share the same
    upstream description; this function does NOT consume the markdown
    (caller may invoke it once per savefig).

    Returns concatenated markdown in chronological order (earliest first),
    cells joined by blank lines, or None if no preceding markdown is found.
    """
    chunks_reversed: list[str] = []
    for i in range(savefig_raw_idx - 1, -1, -1):
        cell = cells[i]
        if cell.cell_type != "markdown":
            continue
        text = _cell_text(cell).strip()
        if not text:
            continue
        chunks_reversed.append(text)
        # Section-header check: first non-blank line starts with '#'.
        first_line = text.split("\n", 1)[0].lstrip()
        if first_line.startswith("#"):
            break
    if not chunks_reversed:
        return None
    return "\n\n".join(reversed(chunks_reversed))


def _walk_notebook_savefigs(
    notebook_path: Path, project_dir: Path
) -> tuple[list[SavefigCall], dict[int, str]]:
    """Walk one notebook for savefig calls.

    Returns (savefig_calls, markdown_cells_by_code_cell_index) where
    markdown_cells is keyed by 1-based CODE-CELL index. A code cell whose
    AST contains at least one savefig call gets walked back via
    `_collect_md_walkback` to collect preceding markdown context up to
    a section header.

    Code cells without savefig calls do NOT get an md_by_code_index
    entry — only savefig-bearing cells contribute to the map.

    Multiple savefigs in adjacent code cells under a single section may
    share the same walked-back markdown; the walk-back is independent
    per code cell.
    """
    import nbformat
    rel_path = str(notebook_path.relative_to(project_dir))
    try:
        nb = nbformat.read(str(notebook_path), as_version=4)
    except Exception:
        return [], {}

    cells = list(nb.cells)
    savefigs: list[SavefigCall] = []
    md_by_code_index: dict[int, str] = {}

    # v0.4 Phase 2: track the most recent code cell's source as the
    # "setup scope" for the next savefig-bearing cell. Markdown cells
    # in between are transparent. Updated after every code cell visit
    # (savefig-bearing or not, parseable or not).
    prev_code_source: Optional[str] = None

    code_index = 0
    for raw_idx, cell in enumerate(cells):
        if cell.cell_type != "code":
            continue
        code_index += 1
        source = _cell_text(cell)
        cleaned = _strip_jupyter_magics(source)
        if not cleaned.strip():
            prev_code_source = source
            continue
        try:
            tree = ast.parse(cleaned)
        except SyntaxError:
            prev_code_source = source
            continue

        # First-pass scan: does this cell contain any savefig calls?
        # If yes, walk back ONCE for markdown context and use for every
        # savefig in this cell.
        cell_savefigs = sorted(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and _is_savefig_call(n)),
            key=lambda n: (n.lineno, n.col_offset),
        )
        if not cell_savefigs:
            prev_code_source = source
            continue

        md_walkback = _collect_md_walkback(cells, raw_idx)
        if md_walkback:
            md_by_code_index[code_index] = md_walkback

        # Walk savefigs in source order so per-savefig AST scopes
        # partition correctly (each savefig sees only the calls between
        # the prior savefig and itself, plus the entire preceding cell).
        prev_savefig_line: Optional[int] = None
        for node in cell_savefigs:
            # First positional arg is the path
            saved = None
            if node.args:
                saved = _last_string_in_path_expr(node.args[0])
            if saved is not None:
                # Reduce to basename for matching
                saved_basename: Optional[str] = Path(saved).name
            else:
                saved_basename = None
            try:
                raw = ast.unparse(node)
                if len(raw) > 200:
                    raw = raw[:197] + "..."
            except Exception:
                raw = "(unparseable)"

            # v0.4 Phase 2: matplotlib AST extraction. Scope =
            # prev_code_source (entirely) + this cell from
            # prev_savefig_line+1 up to and including node.lineno.
            extraction = _extract_plot_calls(
                setup_cell_source=prev_code_source,
                savefig_cell_source=cleaned,
                savefig_line=node.lineno,
                prev_savefig_line=prev_savefig_line,
            )
            plot_calls = None if extraction.is_empty() else extraction

            savefigs.append(SavefigCall(
                notebook=rel_path,
                cell=code_index,
                line=node.lineno,
                saved_basename=saved_basename,
                raw_call=raw,
                preceding_md_cell_index=code_index if code_index in md_by_code_index else None,
                plot_calls=plot_calls,
            ))
            prev_savefig_line = node.lineno

        prev_code_source = source

    return savefigs, md_by_code_index


# ---------------------------------------------------------------------------
# Figure inventory
# ---------------------------------------------------------------------------

# Directories where figures might live, relative to project_dir.
_FIGURE_DIR_CANDIDATES = ("figures", "figs", "plots", "output/figures", "results/figures")


def find_figures_dirs(project_dir: Path) -> list[Path]:
    """Return all candidate figure directories that exist."""
    found: list[Path] = []
    for cand in _FIGURE_DIR_CANDIDATES:
        p = project_dir / cand
        if p.is_dir():
            found.append(p)
    return found


def find_figure_files(project_dir: Path) -> list[Path]:
    """Walk all candidate figure dirs and return image files (sorted)."""
    out: set[Path] = set()
    for fd in find_figures_dirs(project_dir):
        for p in fd.rglob("*"):
            if p.is_file() and is_image_file(p) and not p.name.startswith("."):
                out.add(p)
    return sorted(out)


def find_notebooks(project_dir: Path) -> list[Path]:
    """Find all .ipynb files (mirrors extract_methods.py logic)."""
    patterns = ["notebooks/*.ipynb", "*.ipynb", "src/*.ipynb", "analysis/*.ipynb"]
    found: set[Path] = set()
    for pat in patterns:
        for p in project_dir.glob(pat):
            if not p.name.startswith("."):
                found.add(p)
    return sorted(found)


# ---------------------------------------------------------------------------
# Caption candidate construction
# ---------------------------------------------------------------------------

def _truncate(text: str, n: int) -> str:
    text = text.strip()
    if len(text) <= n:
        return text
    return text[:n - 3].rstrip() + "..."


def _last_paragraph(text: str) -> str:
    """Return the last non-empty paragraph of a markdown cell."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return ""
    return paragraphs[-1]


def build_figure_records(
    figure_files: list[Path],
    project_dir: Path,
    report_refs: list[ReportImageRef],
    notebook_savefigs: list[SavefigCall],
    notebook_md_by_code_index: dict[str, dict[int, str]],
) -> list[FigureRecord]:
    """For each figure file, attach captions and savefig origins."""
    # Index report refs by basename for fast lookup.
    report_by_basename: dict[str, list[ReportImageRef]] = {}
    for r in report_refs:
        bn = Path(r.url).name
        report_by_basename.setdefault(bn, []).append(r)

    # Index savefig calls by basename.
    savefig_by_basename: dict[str, list[SavefigCall]] = {}
    for s in notebook_savefigs:
        if s.saved_basename:
            savefig_by_basename.setdefault(s.saved_basename, []).append(s)

    out: list[FigureRecord] = []
    for fp in figure_files:
        rel = str(fp.relative_to(project_dir))
        fname = fp.name
        try:
            size = fp.stat().st_size
        except OSError:
            size = 0
        rec = FigureRecord(
            path=rel,
            filename=fname,
            size_bytes=size,
            format=infer_image_format(fp),
        )

        # 1. REPORT.md captions (highest priority)
        for r in report_by_basename.get(fname, []):
            if r.alt_text:
                rec.captions.append(CaptionCandidate(
                    source="report",
                    text=_truncate(r.alt_text, 280),
                    context={"line": r.line, "section": r.section},
                ))

        # 2. Notebook savefig + preceding markdown context
        first_walkback: Optional[str] = None
        first_walkback_nb: Optional[str] = None
        # v0.4 Phase 2: capture the FIRST non-empty matplotlib AST
        # extraction across this figure's savefig origins. First-wins is
        # consistent with how notebook_prose is selected — avoids
        # combining title/panels from multiple notebooks (which would
        # require deduplication of conflicting signals).
        first_plot_calls: Optional[PlotCallExtraction] = None
        first_plot_calls_nb: Optional[str] = None
        for s in savefig_by_basename.get(fname, []):
            rec.savefig_origins.append(SavefigOrigin(
                notebook=s.notebook,
                cell=s.cell,
                line=s.line,
                raw_call=s.raw_call,
            ))
            md_for_nb = notebook_md_by_code_index.get(s.notebook, {})
            preceding_md = md_for_nb.get(s.cell)
            if preceding_md:
                last_para = _last_paragraph(preceding_md)
                if last_para:
                    # Short caption candidate (existing v1 behavior;
                    # 280-char cap, last-paragraph reduction).
                    rec.captions.append(CaptionCandidate(
                        source="notebook_md",
                        text=_truncate(last_para, 280),
                        context={
                            "notebook": s.notebook,
                            "preceding_cell": s.cell,
                        },
                    ))
                # CaptionDescriptor.notebook_prose: full unredacted walk-back
                # (v0.4 Phase 1b / inventory schema v2). First non-empty
                # wins when a figure is saved by multiple savefigs in
                # different notebooks; subsequent origins are recorded in
                # savefig_origins but their walk-backs aren't concatenated
                # (avoids duplication when notebooks repeat the same prose).
                if first_walkback is None:
                    first_walkback = preceding_md
                    first_walkback_nb = s.notebook
            if s.plot_calls is not None and first_plot_calls is None:
                first_plot_calls = s.plot_calls
                first_plot_calls_nb = s.notebook

        if first_walkback is not None:
            rec.description.notebook_prose = first_walkback[:_DESCRIPTION_TEXT_CAP]
            rec.description.source_refs.append(
                f"notebook_md_walkback({first_walkback_nb})"
            )
        if first_plot_calls is not None:
            # Merge AST extraction into descriptor. notebook_prose stays
            # as set above (separate provenance); title/axes/legend/panels
            # come from the AST.
            if rec.description.title is None and first_plot_calls.title:
                rec.description.title = first_plot_calls.title
            for label in first_plot_calls.axes_labels:
                if label not in rec.description.axes_labels:
                    rec.description.axes_labels.append(label)
            for lab in first_plot_calls.legend_labels:
                if lab not in rec.description.legend_labels:
                    rec.description.legend_labels.append(lab)
            if not rec.description.panels and first_plot_calls.panels:
                rec.description.panels = list(first_plot_calls.panels)
            rec.description.source_refs.append(
                f"matplotlib_ast({first_plot_calls_nb})"
            )

        # 3. Filename-derived (always available)
        rec.captions.append(CaptionCandidate(
            source="filename",
            text=filename_to_caption(fname),
            context={},
        ))

        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Top-level extraction
# ---------------------------------------------------------------------------

def extract_figures(project_dir: Path) -> FigureInventoryReport:
    """Run the full figure-extraction pipeline against a project directory."""
    figures = find_figure_files(project_dir)
    figures_dirs = [
        str(d.relative_to(project_dir)) for d in find_figures_dirs(project_dir)
    ]

    # REPORT.md image references
    report_refs: list[ReportImageRef] = []
    report_path = project_dir / "REPORT.md"
    if report_path.is_file():
        try:
            report_refs = parse_report_image_references(
                report_path.read_text(encoding="utf-8")
            )
        except OSError:
            pass

    # Notebook savefig calls + per-notebook markdown mappings
    all_savefigs: list[SavefigCall] = []
    md_by_notebook: dict[str, dict[int, str]] = {}
    for nb in find_notebooks(project_dir):
        savefigs, md_map = _walk_notebook_savefigs(nb, project_dir)
        all_savefigs.extend(savefigs)
        rel = str(nb.relative_to(project_dir))
        md_by_notebook[rel] = md_map

    figure_records = build_figure_records(
        figures, project_dir, report_refs, all_savefigs, md_by_notebook,
    )
    return FigureInventoryReport(
        project_dir=str(project_dir),
        figures_dirs=figures_dirs,
        figures=figure_records,
    )


# ---------------------------------------------------------------------------
# figures_inventory.md formatter
# ---------------------------------------------------------------------------

def format_figures_inventory_md(report: FigureInventoryReport) -> str:
    """Render the figure inventory as a human-readable markdown document.

    The Figure-selection prompt (Phase 3) consumes this to choose 4–8
    figures that support the chosen throughline. The format prioritizes
    the REPORT-derived caption (when available) and the notebook origin.
    """
    out: list[str] = []
    # v0.4 inventory schema v2: parseable comment for downstream tooling
    # to detect schema version. v1 had no header comment.
    out.append("<!-- inventory_schema_version: 2 -->")
    out.append("# Figures Inventory")
    out.append("")
    out.append(
        f"Auto-generated from `extract_figures.py` over `{report.project_dir}`. "
        f"Each figure below comes with caption candidates ranked by source: "
        f"REPORT-derived first (project's own authored caption), then "
        f"notebook-context (preceding markdown cell), then filename-derived "
        f"as a fallback. v0.4+ inventories also carry a structured "
        f"**Description** block (Tier 8 caption-richness) when notebook "
        f"walk-back, matplotlib AST, or REPORT prose yields signal beyond "
        f"the short caption. The Figure-selection prompt picks 4–8 figures "
        f"from this inventory based on the chosen throughline; figures NOT "
        f"in this inventory cannot be embedded (per SPEC §6 / D-004 — no "
        f"figure regeneration in v1)."
    )
    out.append("")

    s = report.to_dict()["summary"]
    out.append("## Summary")
    out.append("")
    out.append(f"- Total figures: **{s['total_figures']}**")
    out.append(f"- Total size: {s['total_size_bytes']:,} bytes")
    fmts = ", ".join(f"{k}: {v}" for k, v in sorted(s["by_format"].items()))
    out.append(f"- Formats: {fmts or '(none)'}")
    out.append(f"- With notebook-savefig origin: {s['with_notebook_origin']}")
    out.append(f"- Referenced in REPORT.md: {s['with_report_reference']}")
    out.append(f"- Filename-only (no notebook or REPORT context): {s['filename_only']}")
    out.append("")

    if report.figures_dirs:
        out.append(
            f"Scanned figure directories: "
            f"{', '.join('`' + d + '`' for d in report.figures_dirs)}"
        )
    else:
        out.append("**No figures directory found** at any of the standard paths.")
    out.append("")

    if not report.figures:
        out.append("_(no figure files found in this project)_")
        return "\n".join(out)

    out.append("## Figures")
    out.append("")
    for fig in report.figures:
        out.append(f"### `{fig.path}`")
        out.append("")
        size_kb = fig.size_bytes / 1024
        out.append(f"_{fig.format.upper()}, {size_kb:.1f} KB_")
        out.append("")
        # Caption candidates, in order of authority
        if fig.captions:
            out.append("**Caption candidates:**")
            out.append("")
            for c in fig.captions:
                src_label = {
                    "report": "REPORT.md",
                    "notebook_md": "notebook context",
                    "filename": "filename",
                }.get(c.source, c.source)
                ctx = ""
                if c.source == "report" and c.context.get("section"):
                    ctx = f" _(in {c.context['section']})_"
                elif c.source == "notebook_md" and c.context.get("notebook"):
                    ctx = (
                        f" _({c.context['notebook']}, preceding cell "
                        f"{c.context.get('preceding_cell')})_"
                    )
                out.append(f"- **{src_label}{ctx}**: {c.text}")
            out.append("")
        # v0.4 Phase 1b: structured Description block (inventory schema v2).
        # Only emitted when CaptionDescriptor has at least one populated
        # field. Phase 1b only fills notebook_prose; later phases (2/3/4)
        # populate title, axes, panels, etc.
        if not fig.description.is_empty():
            out.append("**Description:**")
            out.append("")
            d = fig.description
            if d.title:
                out.append(f"- _Title:_ {d.title}")
            if d.axes_labels:
                out.append(f"- _Axes:_ {'; '.join(d.axes_labels)}")
            if d.legend_labels:
                out.append(f"- _Legend:_ {'; '.join(d.legend_labels)}")
            if d.panels:
                # v0.4 Phase 3: separator is `; ` (consistent with Axes
                # and Legend bullets); the panel-entry parser in
                # paper_writer_helpers._parse_one_description_block uses
                # `;` as the boundary token. Comma-separation would break
                # parsing on titles that contain commas (e.g. "(A) Foo,
                # bar; (B) Baz").
                panel_summary = "; ".join(
                    f"({p.letter})" + (f" {p.title}" if p.title else "")
                    for p in d.panels
                )
                out.append(f"- _Panels:_ {panel_summary}")
            if d.notebook_prose:
                out.append("")
                out.append("_Notebook prose:_")
                out.append("")
                # Render as blockquote so multi-line prose is visually
                # distinct from inventory metadata and won't be misparsed
                # as further bullet items by lenient markdown consumers.
                for line in d.notebook_prose.split("\n"):
                    out.append(f"> {line}" if line else ">")
                out.append("")
            if d.source_refs:
                out.append(
                    f"_Source refs:_ {', '.join(d.source_refs)}"
                )
            out.append("")
        # Savefig origins
        if fig.savefig_origins:
            out.append("**Generated by:**")
            out.append("")
            for o in fig.savefig_origins:
                out.append(
                    f"- `{o.notebook}` cell {o.cell}, line {o.line}: "
                    f"`{o.raw_call}`"
                )
            out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="extract_figures.py",
        description=(
            "Inventory figures in a BERIL project and gather caption "
            "candidates from REPORT.md / notebook savefig context / "
            "filename. Writes JSON to stdout and (optionally) "
            "figures_inventory.md to --output-dir. Selection of which "
            "4–8 figures to embed is done downstream by a prompt."
        ),
    )
    p.add_argument(
        "project_dir",
        type=Path,
        help="Path to the BERIL project directory (projects/<id>/).",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Directory to write figures_inventory.md (default: do not "
            "write a file; JSON-only)."
        ),
    )
    p.add_argument(
        "--no-md",
        action="store_true",
        help="Suppress figures_inventory.md write even if --output-dir set.",
    )
    args = p.parse_args(argv)

    if not args.project_dir.is_dir():
        print(
            f"Error: project_dir does not exist or is not a directory: "
            f"{args.project_dir}",
            file=sys.stderr,
        )
        return 1

    report = extract_figures(args.project_dir)
    payload = json.dumps(report.to_dict(), indent=2)
    sys.stdout.write(payload + "\n")

    if args.output_dir is not None and not args.no_md:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        md_path = args.output_dir / "figures_inventory.md"
        md_path.write_text(format_figures_inventory_md(report), encoding="utf-8")
        print(f"Wrote figures_inventory.md to {md_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
