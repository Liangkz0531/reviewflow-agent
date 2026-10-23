"""Review Agent: wrap static analysis in a workflow node."""

from __future__ import annotations

from time import perf_counter

from reviewflow.agents.base import failed_output
from reviewflow.models import (
    AgentName,
    InputMode,
    Issue,
    MessageKind,
    NodeOutput,
    NodeStatus,
    ReviewArtifact,
    Severity,
    WorkflowState,
)
from reviewflow.static_review import StaticReviewAgent

_SEVERITY_RANK = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2}


class ReviewAgent:
    """Review current code and return a structured blocking/pass decision."""

    name = AgentName.REVIEW_AGENT

    def __init__(self, static_reviewer: StaticReviewAgent) -> None:
        self._static_reviewer = static_reviewer

    def run(self, state: WorkflowState) -> NodeOutput:
        started = perf_counter()
        requirement = (
            state.input.requirement
            if state.input.mode == InputMode.REQUIREMENT
            else state.input.requirement or None
        )
        try:
            result = self._static_reviewer.review(
                state.current_code,
                requirement=requirement,
                enable_ruff=state.input.enable_ruff,
                enable_mypy=state.input.enable_mypy,
                strict=state.input.strict,
            )
            threshold = _SEVERITY_RANK[state.input.repair_threshold]
            blocking_issues: list[Issue] = [
                issue
                for issue in result.report.issues
                if _SEVERITY_RANK[issue.severity] >= threshold
            ]
            artifact = ReviewArtifact(
                review_round=len(state.review_history) + 1,
                report=result.report,
                passed=not blocking_issues,
                blocking_issues=blocking_issues,
                steps=result.steps,
                tool_results=result.tool_results,
                plan_reason=result.plan_reason,
                used_requirement=bool(requirement),
            )
        except Exception as exc:
            return failed_output(
                sender=self.name,
                recipient=AgentName.ORCHESTRATOR,
                kind=MessageKind.FAILED,
                summary="审查 Agent 执行失败。",
                error=str(exc),
                started=started,
            )

        verdict = "通过" if artifact.passed else f"发现 {len(blocking_issues)} 个阻断问题"
        return NodeOutput(
            sender=self.name,
            recipient=AgentName.ORCHESTRATOR,
            kind=MessageKind.REVIEW_COMPLETED,
            summary=f"第 {artifact.review_round} 轮审查：{verdict}。",
            payload=artifact.model_dump(mode="json"),
            status=NodeStatus.COMPLETED,
            duration_ms=int((perf_counter() - started) * 1000),
        )
