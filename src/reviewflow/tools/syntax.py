"""AST-based syntax and lightweight semantic checks."""

from __future__ import annotations

import ast
import time

from reviewflow.models import IssueCandidate, IssueCategory, Severity, ToolResult
from reviewflow.tools.base import StaticTool


def _line(node: ast.AST) -> int | None:
    return getattr(node, "lineno", None)


def _column(node: ast.AST) -> int | None:
    return getattr(node, "col_offset", None)


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_literal_zero(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == 0


class _ReviewVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[IssueCandidate] = []

    def _add(
        self,
        node: ast.AST,
        *,
        severity: Severity,
        category: IssueCategory,
        title: str,
        explanation: str,
        suggestion: str,
        evidence: list[str],
    ) -> None:
        self.findings.append(
            IssueCandidate(
                severity=severity,
                category=category,
                line=_line(node),
                column=(_column(node) or 0) + 1 or None,
                title=title,
                explanation=explanation,
                suggestion=suggestion,
                evidence=evidence,
            )
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_mutable_defaults(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_mutable_defaults(node)
        self.generic_visit(node)

    def _check_mutable_defaults(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        defaults = list(node.args.defaults) + [
            default for default in node.args.kw_defaults if default is not None
        ]
        for default in defaults:
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self._add(
                    default,
                    severity=Severity.HIGH,
                    category=IssueCategory.LOGIC,
                    title="函数使用了可变默认参数",
                    explanation=(
                        "列表、字典或集合作为默认参数时会在多次函数调用之间共享，"
                        "可能导致难以发现的状态污染。"
                    ),
                    suggestion="将默认值改为 None，并在函数内部创建新的可变对象。",
                    evidence=["AST: mutable-default"],
                )

    def visit_Compare(self, node: ast.Compare) -> None:
        for op, comparator in zip(node.ops, node.comparators, strict=False):
            if isinstance(op, (ast.Eq, ast.NotEq)) and _is_none(comparator):
                self._add(
                    node,
                    severity=Severity.MEDIUM,
                    category=IssueCategory.LOGIC,
                    title="与 None 比较时使用了相等运算符",
                    explanation="None 应该使用 is 或 is not 判断，而不是 == 或 !=。",
                    suggestion="改为 `value is None` 或 `value is not None`。",
                    evidence=["AST: none-comparison"],
                )
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and _is_literal_zero(
            node.right
        ):
            self._add(
                node,
                severity=Severity.HIGH,
                category=IssueCategory.EDGE_CASE,
                title="除数或模数为字面量 0",
                explanation="该运算会在运行时触发 ZeroDivisionError。",
                suggestion="修改除数，或在运算前检查分母并处理 0 的情况。",
                evidence=["AST: literal-zero-division"],
            )
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self._add(
                node,
                severity=Severity.MEDIUM,
                category=IssueCategory.LOGIC,
                title="使用了裸 except",
                explanation="裸 except 会捕获所有异常，包括系统退出和键盘中断，容易掩盖真实错误。",
                suggestion="捕获明确的异常类型，并在必要时记录异常信息。",
                evidence=["AST: bare-except"],
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
            self._add(
                node,
                severity=Severity.HIGH,
                category=IssueCategory.SECURITY,
                title=f"使用了 {node.func.id}",
                explanation="动态执行字符串代码会带来明显的代码注入风险。",
                suggestion="避免使用 eval 或 exec，改用明确的数据解析和业务逻辑。",
                evidence=[f"AST: {node.func.id}"],
            )
        self.generic_visit(node)


class SyntaxTool(StaticTool):
    """Parse Python code and run a few high-value AST checks."""

    name = "syntax"
    description = "AST syntax parsing and lightweight semantic checks"

    def analyze(self, code: str) -> ToolResult:
        started = time.perf_counter()
        try:
            tree = ast.parse(code, filename="user_code.py", type_comments=True)
        except SyntaxError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            finding = IssueCandidate(
                severity=Severity.HIGH,
                category=IssueCategory.SYNTAX,
                line=exc.lineno,
                column=exc.offset,
                title="Python 语法错误",
                explanation=exc.msg or "代码无法被 Python 解析。",
                suggestion="先根据错误位置修复语法，再运行其他静态检查。",
                evidence=["AST: SyntaxError"],
            )
            return ToolResult(
                tool_name=self.name,
                success=True,
                duration_ms=duration_ms,
                findings=[finding],
                raw_output=f"{exc.__class__.__name__}: {exc.msg}",
                metadata={"syntax_ok": False},
            )

        visitor = _ReviewVisitor()
        visitor.visit(tree)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ToolResult(
            tool_name=self.name,
            success=True,
            duration_ms=duration_ms,
            findings=visitor.findings,
            raw_output=f"Parsed {len(code.splitlines())} lines with AST.",
            metadata={
                "syntax_ok": True,
                "line_count": len(code.splitlines()),
                "function_count": sum(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    for node in ast.walk(tree)
                ),
            },
        )
