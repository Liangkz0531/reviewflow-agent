"""End-to-end state-transition tests for the lightweight orchestrator."""

from __future__ import annotations

from collections.abc import Callable

from reviewflow.models import (
    AgentName,
    CodeArtifact,
    FixArtifact,
    InputMode,
    Issue,
    MessageKind,
    NodeOutput,
    NodeStatus,
    ReviewArtifact,
    ReviewReport,
    Severity,
    WorkflowInput,
    WorkflowState,
    WorkflowStatus,
)
from reviewflow.orchestrator import WorkflowOrchestrator


class CallableNode:
    """Small workflow-node test double."""

    def __init__(
        self,
        name: AgentName,
        callback: Callable[[WorkflowState], NodeOutput],
    ) -> None:
        self.name = name
        self.callback = callback
        self.calls = 0

    def run(self, state: WorkflowState) -> NodeOutput:
        self.calls += 1
        return self.callback(state)


def code_output(code: str = "value = 1\n") -> NodeOutput:
    artifact = CodeArtifact(summary="已生成代码。", code=code)
    return NodeOutput(
        sender=AgentName.CODING_AGENT,
        recipient=AgentName.REVIEW_AGENT,
        kind=MessageKind.CODE_READY,
        summary=artifact.summary,
        payload=artifact.model_dump(mode="json"),
    )


def issue() -> Issue:
    return Issue(
        severity=Severity.MEDIUM,
        title="潜在问题",
        explanation="用于测试的条件阻断问题。",
    )


def review_output(review_round: int, *, passed: bool) -> NodeOutput:
    blocking = [] if passed else [issue()]
    artifact = ReviewArtifact(
        review_round=review_round,
        report=ReviewReport(
            summary="通过。" if passed else "需要修复。",
            issues=blocking,
        ),
        passed=passed,
        blocking_issues=blocking,
    )
    return NodeOutput(
        sender=AgentName.REVIEW_AGENT,
        recipient=AgentName.ORCHESTRATOR,
        kind=MessageKind.REVIEW_COMPLETED,
        summary=f"第 {review_round} 轮审查。",
        payload=artifact.model_dump(mode="json"),
    )


def fix_output(code: str) -> NodeOutput:
    artifact = FixArtifact(
        summary="已修复问题。",
        code=code,
        changes=["调整实现"],
        addressed_issue_ids=[],
    )
    return NodeOutput(
        sender=AgentName.FIX_AGENT,
        recipient=AgentName.REVIEW_AGENT,
        kind=MessageKind.FIX_COMPLETED,
        summary=artifact.summary,
        payload=artifact.model_dump(mode="json"),
    )


def never_call(state: WorkflowState) -> NodeOutput:
    raise AssertionError(f"不应调用该节点：{state.run_id}")


def test_existing_code_passes_without_calling_coding_or_fix() -> None:
    coding = CallableNode(AgentName.CODING_AGENT, never_call)
    review = CallableNode(
        AgentName.REVIEW_AGENT,
        lambda state: review_output(1, passed=True),
    )
    fix = CallableNode(AgentName.FIX_AGENT, never_call)
    orchestrator = WorkflowOrchestrator(coding, review, fix)

    result = orchestrator.run(
        WorkflowInput(mode=InputMode.EXISTING_CODE, initial_code="value = 1\n")
    )

    assert result.status == WorkflowStatus.COMPLETED
    assert result.final_code == "value = 1"
    assert result.final_review is not None and result.final_review.passed
    assert coding.calls == 0
    assert review.calls == 1
    assert fix.calls == 0
    assert any(message.kind == MessageKind.WORKFLOW_FINISHED for message in result.messages)


def test_requirement_mode_runs_coding_then_review() -> None:
    coding = CallableNode(AgentName.CODING_AGENT, lambda state: code_output())
    review = CallableNode(
        AgentName.REVIEW_AGENT,
        lambda state: review_output(1, passed=True),
    )
    fix = CallableNode(AgentName.FIX_AGENT, never_call)
    orchestrator = WorkflowOrchestrator(coding, review, fix)

    result = orchestrator.run(
        WorkflowInput(mode=InputMode.REQUIREMENT, requirement="实现一个简单函数。")
    )

    assert result.status == WorkflowStatus.COMPLETED
    assert coding.calls == 1
    assert result.code_artifact is not None


def test_failed_review_runs_fix_and_second_review() -> None:
    coding = CallableNode(AgentName.CODING_AGENT, lambda state: code_output("x = 1\n"))

    def review_callback(state: WorkflowState) -> NodeOutput:
        round_number = len(state.review_history) + 1
        return review_output(round_number, passed=round_number == 2)

    review = CallableNode(AgentName.REVIEW_AGENT, review_callback)
    fix = CallableNode(AgentName.FIX_AGENT, lambda state: fix_output("x = 2\n"))
    orchestrator = WorkflowOrchestrator(coding, review, fix)

    result = orchestrator.run(
        WorkflowInput(mode=InputMode.EXISTING_CODE, initial_code="x = 1\n")
    )

    assert result.status == WorkflowStatus.COMPLETED
    assert review.calls == 2
    assert fix.calls == 1
    assert result.repair_attempts == 1
    assert result.final_code == "x = 2\n"
    assert [item.kind for item in result.messages if item.sender == AgentName.REVIEW_AGENT] == [
        MessageKind.REVIEW_COMPLETED,
        MessageKind.REVIEW_COMPLETED,
    ]


def test_max_repair_attempts_stops_with_remaining_issues() -> None:
    coding = CallableNode(AgentName.CODING_AGENT, lambda state: code_output("x = 1\n"))
    review = CallableNode(
        AgentName.REVIEW_AGENT,
        lambda state: review_output(len(state.review_history) + 1, passed=False),
    )
    fix = CallableNode(AgentName.FIX_AGENT, lambda state: fix_output("x = 2\n"))
    orchestrator = WorkflowOrchestrator(coding, review, fix)

    result = orchestrator.run(
        WorkflowInput(
            mode=InputMode.EXISTING_CODE,
            initial_code="x = 1\n",
            max_repair_attempts=1,
        )
    )

    assert result.status == WorkflowStatus.MAX_ITERATIONS_REACHED
    assert result.repair_attempts == 1
    assert len(result.review_history) == 2
    assert result.final_review is not None
    assert result.final_review.blocking_issues


def test_invalid_existing_code_fails_before_review() -> None:
    coding = CallableNode(AgentName.CODING_AGENT, never_call)
    review = CallableNode(AgentName.REVIEW_AGENT, never_call)
    fix = CallableNode(AgentName.FIX_AGENT, never_call)
    orchestrator = WorkflowOrchestrator(coding, review, fix)

    result = orchestrator.run(
        WorkflowInput(mode=InputMode.EXISTING_CODE, initial_code="def broken(:\n")
    )

    assert result.status == WorkflowStatus.FAILED
    assert result.failure_reason is not None and "语法无效" in result.failure_reason
    assert review.calls == 0


def test_failed_agent_output_stops_workflow() -> None:
    coding = CallableNode(AgentName.CODING_AGENT, lambda state: code_output())
    review = CallableNode(
        AgentName.REVIEW_AGENT,
        lambda state: NodeOutput(
            sender=AgentName.REVIEW_AGENT,
            recipient=AgentName.ORCHESTRATOR,
            kind=MessageKind.FAILED,
            summary="工具执行失败。",
            payload={"error": "模拟失败"},
            status=NodeStatus.FAILED,
        ),
    )
    fix = CallableNode(AgentName.FIX_AGENT, never_call)
    orchestrator = WorkflowOrchestrator(coding, review, fix)

    result = orchestrator.run(
        WorkflowInput(mode=InputMode.EXISTING_CODE, initial_code="x = 1\n")
    )

    assert result.status == WorkflowStatus.FAILED
    assert result.failure_reason == "模拟失败"
    assert fix.calls == 0

