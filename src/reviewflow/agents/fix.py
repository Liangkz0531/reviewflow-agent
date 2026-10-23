"""Fix Agent: repair the current file using blocking review findings."""

from __future__ import annotations

from time import perf_counter

from reviewflow.agents.base import StructuredOutputAgent, failed_output
from reviewflow.models import (
    AgentName,
    FixArtifact,
    MessageKind,
    NodeOutput,
    NodeStatus,
    WorkflowState,
)
from reviewflow.prompts import (
    FIX_SYSTEM_PROMPT,
    build_code_syntax_retry_prompt,
    build_fix_prompt,
)
from reviewflow.validation import get_syntax_error, validate_python_code


class FixAgent(StructuredOutputAgent):
    """Produce a complete repaired file for the current blocking issues."""

    name = AgentName.FIX_AGENT

    def run(self, state: WorkflowState) -> NodeOutput:
        started = perf_counter()
        if state.current_review is None or not state.current_review.blocking_issues:
            return failed_output(
                sender=self.name,
                recipient=AgentName.REVIEW_AGENT,
                kind=MessageKind.FAILED,
                summary="修复 Agent 缺少阻断问题上下文。",
                error="没有可修复的审查问题。",
                started=started,
            )

        prompt = build_fix_prompt(
            state.input.requirement,
            state.current_code,
            state.current_review.blocking_issues,
            state.fix_history,
        )
        try:
            artifact = self._complete_model(
                FixArtifact,
                system_prompt=FIX_SYSTEM_PROMPT,
                prompt=prompt,
            )
            syntax_error = get_syntax_error(artifact.code)
            if syntax_error:
                retry_prompt = build_code_syntax_retry_prompt(
                    prompt,
                    artifact.code,
                    syntax_error,
                )
                artifact = self._complete_model(
                    FixArtifact,
                    system_prompt=FIX_SYSTEM_PROMPT,
                    prompt=retry_prompt,
                )
            artifact = artifact.model_copy(
                update={
                    "code": validate_python_code(
                        artifact.code,
                        max_code_chars=self._max_code_chars,
                    )
                }
            )
        except Exception as exc:
            return failed_output(
                sender=self.name,
                recipient=AgentName.REVIEW_AGENT,
                kind=MessageKind.FAILED,
                summary="修复 Agent 执行失败。",
                error=str(exc),
                started=started,
            )

        return NodeOutput(
            sender=self.name,
            recipient=AgentName.REVIEW_AGENT,
            kind=MessageKind.FIX_COMPLETED,
            summary=artifact.summary,
            payload=artifact.model_dump(mode="json"),
            status=NodeStatus.COMPLETED,
            duration_ms=int((perf_counter() - started) * 1000),
        )

