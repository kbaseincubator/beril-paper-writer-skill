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
class FigureRecord:
    """All metadata for one figure file."""

    path: str             # relative to project_dir
    filename: str
    size_bytes: int
    format: str           # "png" | "jpeg" | etc.
    captions: list[CaptionCandidate] = field(default_factory=list)
    savefig_origins: list[SavefigOrigin] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "format": self.format,
            "captions": [c.to_dict() for c in self.captions],
            "savefig_origins": [s.to_dict() for s in self.savefig_origins],
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


@dataclass
class SavefigCall:
    """One savefig call discovered in a notebook cell."""

    notebook: str
    cell: int
    line: int
    saved_basename: Optional[str]   # extracted figure filename, if recoverable
    raw_call: str
    preceding_md_cell_index: Optional[int]


def _walk_notebook_savefigs(
    notebook_path: Path, project_dir: Path
) -> tuple[list[SavefigCall], dict[int, str]]:
    """Walk one notebook for savefig calls.

    Returns (savefig_calls, markdown_cells_by_index) where markdown_cells
    is keyed by CODE-CELL index (so a code cell at index N's preceding
    markdown cell is in the dict at key N).
    """
    import nbformat
    rel_path = str(notebook_path.relative_to(project_dir))
    try:
        nb = nbformat.read(str(notebook_path), as_version=4)
    except Exception:
        return [], {}

    cells = list(nb.cells)
    savefigs: list[SavefigCall] = []

    # Map: code-cell index → text of the most recent preceding markdown cell.
    # We iterate in document order, tracking the last-seen markdown cell.
    last_md_text: Optional[str] = None
    md_by_code_index: dict[int, str] = {}
    code_index = 0
    for cell in cells:
        if cell.cell_type == "markdown":
            text = cell.source if isinstance(cell.source, str) else "".join(cell.source)
            if text.strip():
                last_md_text = text.strip()
        elif cell.cell_type == "code":
            code_index += 1
            if last_md_text is not None:
                md_by_code_index[code_index] = last_md_text
                last_md_text = None  # consume — only attribute to one code cell

    # Now AST-walk each code cell looking for savefig calls.
    code_index = 0
    for cell in cells:
        if cell.cell_type != "code":
            continue
        code_index += 1
        source = cell.source if isinstance(cell.source, str) else "".join(cell.source)
        cleaned = _strip_jupyter_magics(source)
        if not cleaned.strip():
            continue
        try:
            tree = ast.parse(cleaned)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_savefig_call(node):
                continue
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
            savefigs.append(SavefigCall(
                notebook=rel_path,
                cell=code_index,
                line=node.lineno,
                saved_basename=saved_basename,
                raw_call=raw,
                preceding_md_cell_index=code_index if code_index in md_by_code_index else None,
            ))

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
                    rec.captions.append(CaptionCandidate(
                        source="notebook_md",
                        text=_truncate(last_para, 280),
                        context={
                            "notebook": s.notebook,
                            "preceding_cell": s.cell,
                        },
                    ))

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
    out.append("# Figures Inventory")
    out.append("")
    out.append(
        f"Auto-generated from `extract_figures.py` over `{report.project_dir}`. "
        f"Each figure below comes with caption candidates ranked by source: "
        f"REPORT-derived first (project's own authored caption), then "
        f"notebook-context (preceding markdown cell), then filename-derived "
        f"as a fallback. The Figure-selection prompt picks 4–8 figures from "
        f"this inventory based on the chosen throughline; figures NOT in "
        f"this inventory cannot be embedded (per SPEC §6 / D-004 — no "
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
