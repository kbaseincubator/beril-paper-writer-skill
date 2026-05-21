"""Tests for orchestrator.write_reframing_log_stub — V1_X_BACKLOG #35.

The stub guarantees reframing_log.md exists before phase_review so the
adversarial reviewer's report_drift detection has the file and does not
raise a spurious missing_section P0. The helper must be idempotent:
it must never clobber a drafter-populated log on pipeline resume.
"""

from __future__ import annotations

from pathlib import Path

from beril_paper_writer.orchestrator import write_reframing_log_stub


class TestWriteReframingLogStub:
    def test_writes_stub_when_absent(self, tmp_path: Path):
        """When reframing_log.md does not exist, the helper writes it
        and returns True."""
        wrote = write_reframing_log_stub(tmp_path)
        assert wrote is True
        path = tmp_path / "reframing_log.md"
        assert path.is_file()

    def test_stub_content_has_heading_and_guidance(self, tmp_path: Path):
        """The stub is an empty-but-valid template: a `# Reframing Log`
        heading plus drafter guidance. The heading matters — the
        adversarial reviewer keys report_drift acknowledgment context
        off the file's structure, not just its presence."""
        write_reframing_log_stub(tmp_path)
        content = (tmp_path / "reframing_log.md").read_text(encoding="utf-8")
        assert content.startswith("# Reframing Log")
        assert "No reframings recorded yet" in content
        assert "report_drift" in content

    def test_idempotent_does_not_clobber_existing(self, tmp_path: Path):
        """When reframing_log.md already exists (e.g. the drafter has
        populated it and the pipeline is resuming), the helper returns
        False and leaves the file untouched. This is the load-bearing
        property: a resume must not wipe drafter-recorded reframings."""
        path = tmp_path / "reframing_log.md"
        drafter_content = (
            "# Reframing Log\n\n"
            "## Entry 1\n"
            "REPORT framed finding 3 as exploratory; manuscript elevates "
            "it to a confirmatory result. Justification: the H3 "
            "replication in notebook 04 closes the gap.\n"
        )
        path.write_text(drafter_content, encoding="utf-8")
        wrote = write_reframing_log_stub(tmp_path)
        assert wrote is False
        assert path.read_text(encoding="utf-8") == drafter_content

    def test_returns_bool(self, tmp_path: Path):
        """Return type is a plain bool — callers branch on it to decide
        whether to emit a log line."""
        first = write_reframing_log_stub(tmp_path)
        second = write_reframing_log_stub(tmp_path)
        assert first is True
        assert second is False
