"""Base interfaces and structured-output handling for workflow agents."""

from __future__ import annotations

from time import perf_counter
from typing import Protocol, TypeVar

from pydantic import BaseModel

from reviewflow.llm import LLMClient, LLMNotConfiguredError, extract_json_object
from reviewflow.models import AgentName, MessageKind, NodeOutput, NodeStatus, WorkflowState
from reviewflow.prompts import (
    STRUCTURED_REPAIR_SYSTEM_PROMPT,
    build_format_retry_prompt,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class AgentOutputError(RuntimeError):
    """Raised when an agent cannot produce valid structured output."""


class WorkflowNode(Protocol):
    """Interface implemented by every workflow node."""

    name: AgentName

    def run(self, state: WorkflowState) -> NodeOutput:
        """Execute the node against shared workflow state."""


class StructuredOutputAgent:
    """Common JSON-output behavior shared by LLM-backed agents."""

    def __init__(self, llm: LLMClient, *, max_code_chars: int = 20_000) -> None:
        self._llm = llm
        self._max_code_chars = max_code_chars

    def _complete_model(
        self,
        model_type: type[ModelT],
        *,
        system_prompt: str,
        prompt: str,
    ) -> ModelT:
        if not self._llm.available:
            raise LLMNotConfiguredError(
                "当前操作需要 DeepSeek API Key，请复制 .env.example 为 .env 并完成配置。"
            )

        response = self._llm.complete(system_prompt, prompt)
        try:
            return model_type.model_validate(extract_json_object(response))
        except Exception as first_error:
            retry_prompt = build_format_retry_prompt(
                prompt,
                response,
                str(first_error),
            )
            retry_response = self._llm.complete(
                STRUCTURED_REPAIR_SYSTEM_PROMPT,
                retry_prompt,
            )
            try:
                return model_type.model_validate(extract_json_object(retry_response))
            except Exception as second_error:
                raise AgentOutputError(
                    f"{model_type.__name__} 输出修复失败：{second_error}"
                ) from second_error


def failed_output(
    *,
    sender: AgentName,
    recipient: AgentName,
    kind: MessageKind,
    summary: str,
    error: str,
    started: float,
) -> NodeOutput:
    """Create a uniform failed node result."""
    return NodeOutput(
        sender=sender,
        recipient=recipient,
        kind=kind,
        summary=summary,
        payload={"error": error},
        status=NodeStatus.FAILED,
        duration_ms=int((perf_counter() - started) * 1000),
    )


