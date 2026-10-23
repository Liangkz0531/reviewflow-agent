"""Unit tests for the three LLM-backed workflow agents."""

from __future__ import annotations

import json
from typing import Any

from reviewflow.agents import CodingAgent, FixAgent, ReviewAgent
from reviewflow.models import (
    InputMode,
    Issue,
    MessageKind,
    NodeStatus,
    ReviewArtifact,
    ReviewReport,
    Severity,
    StaticReviewResult,
    WorkflowInput,
    WorkflowState,
)


class FakeLLM:
    """Deterministic LLM double backed by a response queue."""

    def __init__(self, responses: list[str], *, available: bool = True) -> None:
        self.responses = responses
        self._available = available
        self.prompts: list[tuple[str, str]] = []

    @property
    def available(self) -> bool:
        return self._available

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append((system_prompt, user_prompt))
        if not self.responses:
            raise AssertionError("FakeLLM 没有剩余响应。")
        return self.responses.pop(0)


def state_for(
    *,
    mode: InputMode = InputMode.REQUIREMENT,
    requirement: str = "实现一个加法函数。",
    code: str = "",
) -> WorkflowState:
    workflow_input = WorkflowInput(
        mode=mode,
        requirement=requirement if mode == InputMode.REQUIREMENT else "",
        initial_code=code if mode == InputMode.EXISTING_CODE else "",
    )
    return WorkflowState(input=workflow_input, current_code=code)


def test_coding_agent_returns_structured_code() -> None:
    response = json.dumps(
        {
            "summary": "已生成加法函数。",
            "code": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "assumptions": [],
        },
        ensure_ascii=False,
    )
    agent = CodingAgent(FakeLLM([response]))

    output = agent.run(state_for())

    assert output.status == NodeStatus.COMPLETED
    assert output.kind == MessageKind.CODE_READY
    assert output.payload["code"].startswith("def add")


def test_coding_agent_repairs_invalid_json_once() -> None:
    valid = json.dumps(
        {
            "summary": "已生成。",
            "code": "value = 1\n",
            "assumptions": [],
        },
        ensure_ascii=False,
    )
    llm = FakeLLM(["not-json", valid])
    agent = CodingAgent(llm)

    output = agent.run(state_for())

    assert output.status == NodeStatus.COMPLETED
    assert len(llm.prompts) == 2


def test_coding_agent_repairs_invalid_python_once() -> None:
    invalid = json.dumps(
        {"summary": "错误代码", "code": "def broken(:\n", "assumptions": []},
        ensure_ascii=False,
    )
    valid = json.dumps(
        {"summary": "修正代码", "code": "value = 1\n", "assumptions": []},
        ensure_ascii=False,
    )
    llm = FakeLLM([invalid, valid])
    agent = CodingAgent(llm)

    output = agent.run(state_for())

    assert output.status == NodeStatus.COMPLETED
    assert output.payload["code"] == "value = 1"
    assert len(llm.prompts) == 2


def test_coding_agent_fails_when_api_key_is_missing() -> None:
    agent = CodingAgent(FakeLLM([], available=False))

    output = agent.run(state_for())

    assert output.status == NodeStatus.FAILED
    assert "API Key" in output.payload["error"]


def test_fix_agent_returns_complete_replacement() -> None:
    response = json.dumps(
        {
            "summary": "修复漏洞。",
            "code": "value = 2\n",
            "changes": ["修正常量"],
            "addressed_issue_ids": ["abc123"],
        },
        ensure_ascii=False,
    )
    issue = Issue(severity=Severity.MEDIUM, title="错误", explanation="测试问题。")
    review = ReviewArtifact(
        review_round=1,
        report=ReviewReport(summary="需要修复。", issues=[issue]),
        passed=False,
        blocking_issues=[issue],
    )
    state = state_for(mode=InputMode.EXISTING_CODE, code="value = 1\n")
    state.current_review = review
    agent = FixAgent(FakeLLM([response]))

    output = agent.run(state)

    assert output.status == NodeStatus.COMPLETED
    assert output.kind == MessageKind.FIX_COMPLETED
    assert output.payload["code"] == "value = 2"


class StaticReviewStub:
    """Return one medium issue for threshold tests."""

    def review(self, code: str, **kwargs: Any) -> StaticReviewResult:
        issue = Issue(
            severity=Severity.MEDIUM,
            title="中等风险",
            explanation="测试阈值。",
        )
        return StaticReviewResult(
            report=ReviewReport(summary="发现问题。", issues=[issue])
        )


def test_review_agent_uses_severity_threshold() -> None:
    medium_state = state_for(mode=InputMode.EXISTING_CODE, code="value = 1\n")
    medium_output = ReviewAgent(StaticReviewStub()).run(medium_state)
    assert medium_output.payload["passed"] is False

    high_state = state_for(mode=InputMode.EXISTING_CODE, code="value = 1\n")
    high_state.input = high_state.input.model_copy(update={"repair_threshold": Severity.HIGH})
    high_output = ReviewAgent(StaticReviewStub()).run(high_state)
    assert high_output.payload["passed"] is True
