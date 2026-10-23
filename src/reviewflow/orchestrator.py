"""Lightweight workflow orchestration for the three ReviewFlow agents."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress

from reviewflow.agents import WorkflowNode
from reviewflow.models import (
    AgentMessage,
    AgentName,
    CodeArtifact,
    FixArtifact,
    InputMode,
    MessageKind,
    NodeOutput,
    NodeStatus,
    ReviewArtifact,
    WorkflowEvent,
    WorkflowInput,
    WorkflowResult,
    WorkflowState,
    WorkflowStatus,
    utc_now,
)
from reviewflow.validation import validate_python_code

EventCallback = Callable[[WorkflowEvent], None]


class WorkflowOrchestrator:
    """Execute coding, review, conditional fixing and re-review as one workflow."""

    def __init__(
        self,
        coding_agent: WorkflowNode,
        review_agent: WorkflowNode,
        fix_agent: WorkflowNode,
        *,
        max_code_chars: int = 20_000,
    ) -> None:
        self._coding_agent = coding_agent
        self._review_agent = review_agent
        self._fix_agent = fix_agent
        self._max_code_chars = max_code_chars

    def run(
        self,
        workflow_input: WorkflowInput,
        *,
        on_event: EventCallback | None = None,
    ) -> WorkflowResult:
        """Run the workflow to a terminal state and return an immutable result."""
        state = WorkflowState(input=workflow_input, status=WorkflowStatus.RUNNING)
        self._record_event(
            state,
            node=AgentName.ORCHESTRATOR,
            status=NodeStatus.COMPLETED,
            summary="Workflow 已启动。",
            detail=self._entry_description(workflow_input),
            on_event=on_event,
        )

        try:
            self._prepare_initial_code(state, on_event=on_event)
            if state.status == WorkflowStatus.RUNNING:
                self._run_agentic_loop(state, on_event=on_event)
        except Exception as exc:
            state.status = WorkflowStatus.FAILED
            state.failure_reason = str(exc)
            self._record_event(
                state,
                node=AgentName.ORCHESTRATOR,
                status=NodeStatus.FAILED,
                summary="Workflow 因未处理异常而停止。",
                detail=str(exc),
                on_event=on_event,
            )

        self._finish(state, on_event=on_event)
        return self._to_result(state)

    def _prepare_initial_code(
        self,
        state: WorkflowState,
        *,
        on_event: EventCallback | None,
    ) -> None:
        if state.input.mode == InputMode.EXISTING_CODE:
            try:
                state.current_code = validate_python_code(
                    state.input.initial_code,
                    max_code_chars=self._max_code_chars,
                )
            except ValueError as exc:
                self._fail_workflow(
                    state,
                    summary="已有代码验证失败。",
                    error=str(exc),
                    on_event=on_event,
                )
                return
            self._record_event(
                state,
                node=AgentName.ORCHESTRATOR,
                status=NodeStatus.COMPLETED,
                summary="已有代码已通过语法验证，跳过编码 Agent。",
                detail=f"代码共 {len(state.current_code.splitlines())} 行。",
                on_event=on_event,
            )
            return

        output = self._coding_agent.run(state)
        self._apply_output(state, output, on_event=on_event)
        if not self._output_succeeded(state, output, on_event=on_event):
            return
        if state.code_artifact is None or not state.current_code:
            self._fail_workflow(
                state,
                summary="编码 Agent 没有返回可用代码。",
                error="code_ready 消息中缺少有效代码。",
                on_event=on_event,
            )

    def _run_agentic_loop(
        self,
        state: WorkflowState,
        *,
        on_event: EventCallback | None,
    ) -> None:
        while state.status == WorkflowStatus.RUNNING:
            review_output = self._review_agent.run(state)
            self._apply_output(state, review_output, on_event=on_event)
            if not self._output_succeeded(state, review_output, on_event=on_event):
                return

            review = state.current_review
            if review is None:
                self._fail_workflow(
                    state,
                    summary="审查 Agent 没有返回可用结果。",
                    error="review_completed 消息中缺少审查结果。",
                    on_event=on_event,
                )
                return

            if review.passed:
                state.status = WorkflowStatus.COMPLETED
                return

            if state.repair_attempts >= state.input.max_repair_attempts:
                state.status = WorkflowStatus.MAX_ITERATIONS_REACHED
                self._record_event(
                    state,
                    node=AgentName.ORCHESTRATOR,
                    status=NodeStatus.COMPLETED,
                    summary="已达到最大修复次数，流程停止。",
                    detail=f"仍存在 {len(review.blocking_issues)} 个阻断问题。",
                    on_event=on_event,
                )
                return

            fix_output = self._fix_agent.run(state)
            self._apply_output(state, fix_output, on_event=on_event)
            if not self._output_succeeded(state, fix_output, on_event=on_event):
                return

    def _apply_output(
        self,
        state: WorkflowState,
        output: NodeOutput,
        *,
        on_event: EventCallback | None,
    ) -> None:
        message = AgentMessage(
            sender=output.sender,
            recipient=output.recipient,
            kind=output.kind,
            summary=output.summary,
            payload=output.payload,
        )
        state.messages.append(message)

        detail = output.payload.get("error", "")
        self._record_event(
            state,
            node=output.sender,
            status=output.status,
            summary=output.summary,
            detail=str(detail or self._message_detail(output)),
            duration_ms=output.duration_ms,
            on_event=on_event,
        )

        if output.status != NodeStatus.COMPLETED:
            return
        if output.kind == MessageKind.CODE_READY:
            code_artifact = CodeArtifact.model_validate(output.payload)
            state.code_artifact = code_artifact
            state.current_code = code_artifact.code
        elif output.kind == MessageKind.REVIEW_COMPLETED:
            review_artifact = ReviewArtifact.model_validate(output.payload)
            state.current_review = review_artifact
            state.review_history.append(review_artifact)
        elif output.kind == MessageKind.FIX_COMPLETED:
            fix_artifact = FixArtifact.model_validate(output.payload)
            state.fix_history.append(fix_artifact)
            state.current_code = fix_artifact.code
            state.repair_attempts += 1

    def _output_succeeded(
        self,
        state: WorkflowState,
        output: NodeOutput,
        *,
        on_event: EventCallback | None,
    ) -> bool:
        if output.status in {NodeStatus.COMPLETED, NodeStatus.SKIPPED}:
            return True
        error = str(output.payload.get("error") or output.summary)
        self._fail_workflow(
            state,
            summary=output.summary,
            error=error,
            on_event=on_event,
        )
        return False

    def _fail_workflow(
        self,
        state: WorkflowState,
        *,
        summary: str,
        error: str,
        on_event: EventCallback | None,
    ) -> None:
        state.status = WorkflowStatus.FAILED
        state.failure_reason = error
        self._record_event(
            state,
            node=AgentName.ORCHESTRATOR,
            status=NodeStatus.FAILED,
            summary=summary,
            detail=error,
            on_event=on_event,
        )

    def _finish(self, state: WorkflowState, *, on_event: EventCallback | None) -> None:
        state.finished_at = state.finished_at or utc_now()
        if state.status == WorkflowStatus.RUNNING:
            state.status = WorkflowStatus.FAILED
            state.failure_reason = state.failure_reason or "Workflow 未产生终止状态。"

        final_message = {
            WorkflowStatus.COMPLETED: "Workflow 已完成，代码通过当前审查阈值。",
            WorkflowStatus.MAX_ITERATIONS_REACHED: "Workflow 已结束，但仍存在阻断问题。",
            WorkflowStatus.FAILED: f"Workflow 执行失败：{state.failure_reason}",
        }.get(state.status, "Workflow 已结束。")
        message = AgentMessage(
            sender=AgentName.ORCHESTRATOR,
            recipient=AgentName.USER,
            kind=(
                MessageKind.WORKFLOW_FINISHED
                if state.status != WorkflowStatus.FAILED
                else MessageKind.FAILED
            ),
            summary=final_message,
            payload={
                "status": state.status.value,
                "repair_attempts": state.repair_attempts,
                "failure_reason": state.failure_reason,
            },
        )
        state.messages.append(message)
        self._record_event(
            state,
            node=AgentName.ORCHESTRATOR,
            status=(
                NodeStatus.COMPLETED
                if state.status != WorkflowStatus.FAILED
                else NodeStatus.FAILED
            ),
            summary=final_message,
            on_event=on_event,
        )

    def _record_event(
        self,
        state: WorkflowState,
        *,
        node: AgentName,
        status: NodeStatus,
        summary: str,
        detail: str = "",
        duration_ms: int = 0,
        on_event: EventCallback | None = None,
    ) -> None:
        event = WorkflowEvent(
            sequence=len(state.events) + 1,
            node=node,
            status=status,
            summary=summary,
            detail=detail,
            duration_ms=duration_ms,
        )
        state.events.append(event)
        if on_event is not None:
            with suppress(Exception):
                # UI callbacks must not change the workflow result.
                on_event(event)

    @staticmethod
    def _entry_description(workflow_input: WorkflowInput) -> str:
        if workflow_input.mode == InputMode.REQUIREMENT:
            return "入口：自然语言需求，先执行编码 Agent。"
        return "入口：已有 Python 代码，跳过编码 Agent。"

    @staticmethod
    def _message_detail(output: NodeOutput) -> str:
        if output.kind == MessageKind.CODE_READY:
            return "编码结果已写入共享状态。"
        if output.kind == MessageKind.REVIEW_COMPLETED:
            return "审查结果已写入共享状态，编排器开始条件路由。"
        if output.kind == MessageKind.FIX_COMPLETED:
            return "修复结果已写入共享状态，准备重新审查。"
        return ""

    @staticmethod
    def _to_result(state: WorkflowState) -> WorkflowResult:
        return WorkflowResult(
            run_id=state.run_id,
            status=state.status,
            input=state.input,
            final_code=state.current_code,
            final_review=state.current_review,
            code_artifact=state.code_artifact,
            review_history=state.review_history,
            fix_history=state.fix_history,
            messages=state.messages,
            events=state.events,
            repair_attempts=state.repair_attempts,
            failure_reason=state.failure_reason,
            started_at=state.started_at,
            finished_at=state.finished_at or state.started_at,
        )



