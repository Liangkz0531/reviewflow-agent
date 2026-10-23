"""Dependency wiring for the ReviewFlow application and tests."""

from __future__ import annotations

from reviewflow.agents import CodingAgent, FixAgent, ReviewAgent
from reviewflow.config import Settings
from reviewflow.llm import DeepSeekClient, LLMClient
from reviewflow.orchestrator import WorkflowOrchestrator
from reviewflow.static_review import StaticReviewAgent
from reviewflow.tools import build_default_registry


def build_orchestrator(
    settings: Settings,
    *,
    llm: LLMClient | None = None,
) -> WorkflowOrchestrator:
    """Build the default three-agent workflow."""
    client = llm or DeepSeekClient(settings)
    static_agent = StaticReviewAgent(
        client,
        build_default_registry(),
        max_code_chars=settings.max_code_chars,
    )
    return WorkflowOrchestrator(
        CodingAgent(client, max_code_chars=settings.max_code_chars),
        ReviewAgent(static_agent),
        FixAgent(client, max_code_chars=settings.max_code_chars),
        max_code_chars=settings.max_code_chars,
    )

