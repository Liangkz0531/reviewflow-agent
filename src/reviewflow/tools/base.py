"""Common interfaces for static analysis tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from reviewflow.models import ToolResult


class StaticTool(ABC):
    """A tool that analyses source code without executing it."""

    name: str
    description: str

    @abstractmethod
    def analyze(self, code: str) -> ToolResult:
        """Analyze source code and return a normalized result."""


class ToolRegistry:
    """Lookup table used by the agent when selecting tools."""

    def __init__(self, tools: Iterable[StaticTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def get(self, name: str) -> StaticTool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)
