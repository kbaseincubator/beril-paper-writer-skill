"""Shipped tool scripts for beril-paper-writer.

Each .py in this package is a standalone script invoked by the shell
orchestrator via `python3 "$SKILL_DIR/tools/<script>.py" <args>`.
Scripts use stdlib + declared runtime deps only; they do NOT import
from `beril_paper_writer` so they remain runnable from a copied
location even if the parent package isn't on sys.path.

Tests import these as modules for unit testing of individual functions.
"""
