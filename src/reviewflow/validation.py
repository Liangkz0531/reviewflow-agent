"""Input and Python syntax validation helpers."""

from __future__ import annotations

import ast


def normalize_code(code: str, *, max_code_chars: int = 20_000) -> str:
    """Normalize line endings and validate common input limits."""
    normalized = code.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("请先输入 Python 代码。")
    if len(normalized) > max_code_chars:
        raise ValueError(
            f"代码长度为 {len(normalized)} 个字符，超过限制 {max_code_chars}。"
        )
    return normalized


def get_syntax_error(code: str) -> str | None:
    """Return a compact syntax error message, or None when parsing succeeds."""
    try:
        ast.parse(code)
    except SyntaxError as exc:
        line = f"第 {exc.lineno} 行" if exc.lineno else "未知行"
        column = f"第 {exc.offset} 列" if exc.offset else "未知列"
        return f"{line}，{column}：{exc.msg}"
    return None


def validate_python_code(code: str, *, max_code_chars: int = 20_000) -> str:
    """Return normalized code or raise ValueError when syntax is invalid."""
    normalized = normalize_code(code, max_code_chars=max_code_chars)
    syntax_error = get_syntax_error(normalized)
    if syntax_error:
        raise ValueError(f"Python 语法无效：{syntax_error}")
    return normalized
