"""Static analysis tools used by the review agent."""

from reviewflow.tools.base import StaticTool, ToolRegistry
from reviewflow.tools.mypy import MypyTool
from reviewflow.tools.ruff import RuffTool
from reviewflow.tools.syntax import SyntaxTool


def build_default_registry() -> ToolRegistry:
    """Create the default registry with all supported static tools."""
    return ToolRegistry([SyntaxTool(), RuffTool(), MypyTool()])


__all__ = [
    "MypyTool",
    "RuffTool",
    "StaticTool",
    "SyntaxTool",
    "ToolRegistry",
    "build_default_registry",
]
