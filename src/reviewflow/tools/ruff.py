"""Adapter for the Ruff static analyzer."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any

from reviewflow.models import IssueCandidate, IssueCategory, Severity, ToolResult
from reviewflow.tools.base import StaticTool


def _category(code: str) -> IssueCategory:
    if code.startswith(("E9", "F8")):
        return IssueCategory.SYNTAX
    if code in {"E711", "E712", "E713", "E714", "E721", "E722", "E731", "E741"}:
        return IssueCategory.LOGIC
    if code.startswith(("F", "B")):
        return IssueCategory.LOGIC
    if code.startswith("S"):
        return IssueCategory.SECURITY
    if code.startswith("SIM"):
        return IssueCategory.MAINTAINABILITY
    if code.startswith(("E", "W", "I", "UP")):
        return IssueCategory.STYLE
    return IssueCategory.MAINTAINABILITY


def _severity(code: str) -> Severity:
    if code.startswith(("E9", "F82", "S")):
        return Severity.HIGH
    if code.startswith(("F", "B")):
        return Severity.MEDIUM
    return Severity.LOW


class RuffTool(StaticTool):
    """Run Ruff against source supplied through standard input."""

    name = "ruff"
    description = "Ruff checks for common Python defects, style issues and unsafe patterns"

    def __init__(self, timeout_seconds: int = 15) -> None:
        self.timeout_seconds = timeout_seconds

    def analyze(self, code: str) -> ToolResult:
        started = time.perf_counter()
        executable = shutil.which("ruff")
        if not executable:
            return ToolResult(
                tool_name=self.name,
                success=False,
                duration_ms=0,
                error="Ruff 未安装或不在当前 PATH 中。",
            )

        command = [
            executable,
            "check",
            "--isolated",
            "--select",
            "E,F,I,UP,B,SIM,S",
            "--output-format",
            "json",
            "--stdin-filename",
            "user_code.py",
            "-",
        ]
        try:
            process = subprocess.run(
                command,
                input=code,
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
                error=f"Ruff 执行超过 {self.timeout_seconds} 秒。",
            )
        except OSError as exc:
            return ToolResult(
                tool_name=self.name,
                success=False,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=f"无法启动 Ruff: {exc}",
            )

        if process.returncode not in (0, 1):
            return ToolResult(
                tool_name=self.name,
                success=False,
                duration_ms=int((time.perf_counter() - started) * 1000),
                raw_output=process.stdout,
                error=process.stderr.strip() or f"Ruff 返回状态码 {process.returncode}。",
            )

        try:
            payload: list[dict[str, Any]] = json.loads(process.stdout or "[]")
        except json.JSONDecodeError as exc:
            return ToolResult(
                tool_name=self.name,
                success=False,
                duration_ms=int((time.perf_counter() - started) * 1000),
                raw_output=process.stdout,
                error=f"无法解析 Ruff JSON 输出: {exc}",
            )

        findings: list[IssueCandidate] = []
        for item in payload:
            code_name = str(item.get("code") or "RUFF")
            location = item.get("location") or {}
            message = str(item.get("message") or "Ruff found a problem.")
            fix = item.get("fix") or {}
            fix_message = fix.get("message") if isinstance(fix, dict) else None
            findings.append(
                IssueCandidate(
                    severity=_severity(code_name),
                    category=_category(code_name),
                    line=location.get("row"),
                    column=location.get("column"),
                    title=f"Ruff {code_name}",
                    explanation=message,
                    suggestion=fix_message or "请根据 Ruff 的提示调整对应代码。",
                    evidence=[f"Ruff: {code_name}"],
                )
            )

        return ToolResult(
            tool_name=self.name,
            success=True,
            duration_ms=int((time.perf_counter() - started) * 1000),
            findings=findings,
            raw_output=process.stdout,
            metadata={"issue_count": len(findings), "return_code": process.returncode},
        )
