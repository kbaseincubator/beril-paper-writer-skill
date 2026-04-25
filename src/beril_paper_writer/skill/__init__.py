"""Shipped skill data for beril-paper-writer.

This package's contents are copied into
`<BERIL_ROOT>/.claude/skills/beril-paper-writer/` by
`beril-paper-writer install-skill`. Only the .py files under
`skill/tools/` are intended to be invoked at runtime (typically by the
shell orchestrator); other directories (`prompts/`, `references/`,
`commands/`) ship as static data.

This `__init__.py` exists so the `skill/` directory is treated as a
Python package, which lets tests import `skill.tools.validate_manuscript`
directly without sys.path tricks.
"""
