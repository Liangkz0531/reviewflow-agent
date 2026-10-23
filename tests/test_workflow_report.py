"""Tests for workflow report and diff rendering."""

from __future__ import annotations

from reviewflow.models import (
    InputMode,
    WorkflowInput,
    WorkflowResult,
    WorkflowStatus,
    utc_now,
)
from reviewflow.services.workflow_report import code_diff, workflow_result_to_markdown


def test_code_diff_contains_changed_lines() -> None:
    diff = code_diff("x = 1\n", "x = 2\n")
    assert "-x = 1" in diff
    assert "+x = 2" in diff


def test_workflow_report_contains_status_and_code() -> None:
    result = WorkflowResult(
        run_id="test-run",
        status=WorkflowStatus.COMPLETED,
        input=WorkflowInput(mode=InputMode.EXISTING_CODE, initial_code="x = 1\n"),
        final_code="x = 1\n",
        started_at=utc_now(),
        finished_at=utc_now(),
    )

    markdown = workflow_result_to_markdown(result)

    assert "test-run" in markdown
    assert "已完成" in markdown
    assert "```python" in markdown
