"""Tests for validation and static-review integration."""

from __future__ import annotations

import json

import pytest

from reviewflow.models import Severity
from reviewflow.static_review import StaticReviewAgent
from reviewflow.tools import SyntaxTool, ToolRegistry
from reviewflow.validation import get_syntax_error, normalize_code, validate_python_code


class FakeLLM:
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


def test_validation_normalizes_and_reports_syntax() -> None:
    assert normalize_code("  x = 1\r\n") == "x = 1"
    assert "第 1 行" in (get_syntax_error("def broken(:\n") or "")
    with pytest.raises(ValueError, match="语法无效"):
        validate_python_code("def broken(:\n")


def test_validation_rejects_empty_and_long_code() -> None:
    with pytest.raises(ValueError, match="输入"):
        normalize_code("   ")
    with pytest.raises(ValueError, match="超过限制"):
        normalize_code("x = 1", max_code_chars=3)


def test_static_review_falls_back_without_llm() -> None:
    agent = StaticReviewAgent(
        FakeLLM([], available=False),
        ToolRegistry([SyntaxTool()]),
    )

    result = agent.review("value = 1\n", enable_ruff=False, enable_mypy=False)

    assert result.report.issues == []
    assert any("API Key" in item for item in result.report.limitations)


def test_static_review_passes_requirement_to_prompt() -> None:
    response = json.dumps(
        {
            "summary": "满足需求。",
            "issues": [],
            "recommended_order": [],
            "limitations": [],
        },
        ensure_ascii=False,
    )
    llm = FakeLLM([response])
    agent = StaticReviewAgent(llm, ToolRegistry([SyntaxTool()]))

    result = agent.review(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        requirement="实现加法函数",
        enable_ruff=False,
        enable_mypy=False,
    )

    assert result.report.summary == "满足需求。"
    assert len(llm.prompts) == 1
    assert "实现加法函数" in llm.prompts[0][1]


def test_static_review_merges_tool_findings_when_llm_reports_nothing() -> None:
    response = json.dumps(
        {
            "summary": "没有额外问题。",
            "issues": [],
            "recommended_order": [],
            "limitations": [],
        },
        ensure_ascii=False,
    )
    agent = StaticReviewAgent(FakeLLM([response]), ToolRegistry([SyntaxTool()]))

    result = agent.review(
        "def f(items=[]):\n    return items\n",
        enable_ruff=False,
        enable_mypy=False,
    )

    assert result.report.issues
    assert result.report.issues[0].severity == Severity.HIGH
