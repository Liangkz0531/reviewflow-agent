"""Adapter for the Mypy static type checker."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from reviewflow.models import IssueCandidate, IssueCategory, Severity, ToolResult
from reviewflow.tools.base import StaticTool

_MYPY_LINE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?:(?P<column>\d+):)? "
    r"(?P<level>error|warning|note): (?P<message>.+?)"
    r"(?: \[(?P<code>[A-Za-z0-9_-]+)\])?$"
)


class MypyTool(StaticTool):
    """Run Mypy against a temporary copy of the submitted source."""

    name = "mypy"
    description = "Static type checking for Python source code"

    def __init__(self, timeout_seconds: int = 15) -> None:
        self.timeout_seconds = timeout_seconds

    def analyze(self, code: str) -> ToolResult:
        started = time.perf_counter()
        executable = shutil.which("mypy")
        if not executable:
            return ToolResult(
                tool_name=self.name,
                success=False,
                duration_ms=0,
                error="Mypy 未安装或不在当前 PATH 中。",
            )

        with tempfile.TemporaryDirectory(prefix="reviewflow-") as temp_dir:
            source_path = Path(temp_dir) / "user_code.py"
            source_path.write_text(code, encoding="utf-8")
            command = [
                executable,
                "--no-error-summary",
                "--show-error-codes",
                "--show-column-numbers",
                str(source_path),
            ]
            try:
                process = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=self.timeout_seconds,
                    check=False,
                    shell=False,
                )
            except subprocess.TimeoutExpired:
                return ToolResult(
                    tool_name=self.name,
                    success=False,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error=f"Mypy 执行超过 {self.timeout_seconds} 秒。",
                )
            except OSError as exc:
                return ToolResult(
                    tool_name=self.name,
                    success=False,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error=f"无法启动 Mypy: {exc}",
                )

        output = "\n".join(part for part in (process.stdout, process.stderr) if part).strip()
        if process.returncode not in (0, 1):
            return ToolResult(
                tool_name=self.name,
                success=False,
                duration_ms=int((time.perf_counter() - started) * 1000),
                raw_output=output,
                error=f"Mypy 返回状态码 {process.returncode}。",
            )

        findings: list[IssueCandidate] = []
        for line in output.splitlines():
            match = _MYPY_LINE.match(line)
            if not match:
                continue
            level = match.group("level")
            if level == "note":
                continue
            code_name = match.group("code") or "mypy"
            findings.append(
                IssueCandidate(
                    severity=Severity.MEDIUM if level == "error" else Severity.LOW,
                    category=IssueCategory.TYPE,
                    line=int(match.group("line")),
                    column=int(match.group("column")) if match.group("column") else None,
                    title=f"Mypy {code_name}",
                    explanation=match.group("message"),
                    suggestion="根据类型检查结果补充类型标注或修正类型不匹配。",
                    evidence=[f"Mypy: {code_name}"],
                )
            )

        return ToolResult(
            tool_name=self.name,
            success=True,
            duration_ms=int((time.perf_counter() - started) * 1000),
            findings=findings,
            raw_output=output,
            metadata={"issue_count": len(findings), "return_code": process.returncode},
        )
