"""Coding Agent: turn a requirement into a complete Python source file."""

from __future__ import annotations

from time import perf_counter

from reviewflow.agents.base import StructuredOutputAgent, failed_output
from reviewflow.models import (
    AgentName,
    CodeArtifact,
    InputMode,
    MessageKind,
    NodeOutput,
    NodeStatus,
    WorkflowState,
)
from reviewflow.prompts import (
    CODING_SYSTEM_PROMPT,
    build_code_syntax_retry_prompt,
    build_coding_prompt,
)
from reviewflow.validation import get_syntax_error, validate_python_code


class CodingAgent(StructuredOutputAgent):
    """Create a complete single-file Python implementation from a requirement."""

    name = AgentName.CODING_AGENT

    def run(self, state: WorkflowState) -> NodeOutput:
        started = perf_counter()
        if state.input.mode != InputMode.REQUIREMENT:
            return NodeOutput(
                sender=self.name,
                recipient=AgentName.REVIEW_AGENT,
                kind=MessageKind.CODE_READY,
                summary="已有代码模式，跳过编码 Agent。",
                payload={"skipped": True},
                status=NodeStatus.SKIPPED,
                duration_ms=0,
            )

        prompt = build_coding_prompt(state.input.requirement)
        try:
            artifact = self._complete_model(
                CodeArtifact,
                system_prompt=CODING_SYSTEM_PROMPT,
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
                    CodeArtifact,
                    system_prompt=CODING_SYSTEM_PROMPT,
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
                summary="编码 Agent 执行失败。",
                error=str(exc),
                started=started,
            )

        return NodeOutput(
            sender=self.name,
            recipient=AgentName.REVIEW_AGENT,
            kind=MessageKind.CODE_READY,
            summary=artifact.summary,
            payload=artifact.model_dump(mode="json"),
            status=NodeStatus.COMPLETED,
            duration_ms=int((perf_counter() - started) * 1000),
        )

