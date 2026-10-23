"""The review agent orchestration loop."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from time import perf_counter
from typing import Literal

from reviewflow.llm import LLMClient, LLMNotConfiguredError, extract_json_object
from reviewflow.models import (
    AgentStep,
    Issue,
    IssueCandidate,
    ReviewReport,
    Severity,
    StaticReviewResult,
    ToolResult,
)
from reviewflow.prompts import (
    FOLLOW_UP_SYSTEM_PROMPT,
    PLANNING_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT,
    build_follow_up_prompt,
    build_review_prompt,
    build_tool_plan_prompt,
)
from reviewflow.tools.base import ToolRegistry


class StaticReviewAgent:
    """Plan, run and synthesize a static Python review."""

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        *,
        max_code_chars: int = 20_000,
        max_steps: int = 5,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._max_code_chars = max_code_chars
        self._max_steps = max_steps

    def review(
        self,
        code: str,
        *,
        requirement: str | None = None,
        enable_ruff: bool = True,
        enable_mypy: bool = True,
        strict: bool = False,
        on_step: Callable[[AgentStep], None] | None = None,
    ) -> StaticReviewResult:
        """Run the complete static review workflow."""
        normalized_code = self._normalize_code(code)
        steps: list[AgentStep] = []
        self._emit(
            steps,
            AgentStep(
                title="输入检查",
                detail=f"已接收 {len(normalized_code.splitlines())} 行 Python 代码，不会执行代码。",
            ),
            on_step,
        )

        syntax_tool = self._registry.get("syntax")
        syntax_result = self._run_tool(syntax_tool.name, normalized_code, steps, on_step)
        tool_results = [syntax_result]

        plan_reason = "语法检查已由 AST 工具完成。"
        syntax_ok = bool(syntax_result.metadata.get("syntax_ok"))
        if syntax_ok:
            selected, plan_reason = self._select_tools(
                normalized_code,
                syntax_result,
                enable_ruff=enable_ruff,
                enable_mypy=enable_mypy,
                strict=strict,
            )
            self._emit(
                steps,
                AgentStep(
                    title="制定审查计划",
                    detail=plan_reason,
                ),
                on_step,
            )
            for tool_name in selected:
                if len(steps) >= self._max_steps:
                    break
                tool_results.append(self._run_tool(tool_name, normalized_code, steps, on_step))
        else:
            self._emit(
                steps,
                AgentStep(
                    title="跳过其他静态工具",
                    detail="代码存在语法错误，先让用户修复语法，再运行 Ruff 或 Mypy。",
                    status="skipped",
                ),
                on_step,
            )

        report, synthesis_detail, synthesis_status = self._synthesize_report(
            normalized_code,
            tool_results,
            requirement=requirement,
            strict=strict,
        )
        self._emit(
            steps,
            AgentStep(
                title="生成审查报告",
                detail=synthesis_detail,
                status=synthesis_status,
            ),
            on_step,
        )

        return StaticReviewResult(
            report=report,
            steps=steps,
            tool_results=tool_results,
            plan_reason=plan_reason,
        )

    def follow_up(self, code: str, report: ReviewReport, question: str) -> str:
        """Answer a question grounded in the latest review report."""
        if not question.strip():
            raise ValueError("追问内容不能为空。")
        if not self._llm.available:
            raise LLMNotConfiguredError("需要配置 DeepSeek API Key 才能使用追问功能。")

        prompt = build_follow_up_prompt(
            code,
            report.model_dump_json(indent=2),
            question.strip(),
        )
        return self._llm.complete(FOLLOW_UP_SYSTEM_PROMPT, prompt).strip()

    def _normalize_code(self, code: str) -> str:
        normalized = code.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise ValueError("请先输入 Python 代码。")
        if len(normalized) > self._max_code_chars:
            raise ValueError(
                f"代码长度为 {len(normalized)} 个字符，超过限制 {self._max_code_chars}。"
            )
        return normalized

    def _select_tools(
        self,
        code: str,
        syntax_result: ToolResult,
        *,
        enable_ruff: bool,
        enable_mypy: bool,
        strict: bool,
    ) -> tuple[list[str], str]:
        enabled: list[str] = []
        if enable_ruff:
            enabled.append("ruff")
        if enable_mypy:
            enabled.append("mypy")
        if not enabled:
            return [], "用户未启用额外的静态工具，仅使用 AST 检查。"

        if not self._llm.available:
            return enabled, "LLM 未配置，使用用户选定的静态工具执行完整检查。"

        try:
            response = self._llm.complete(
                PLANNING_SYSTEM_PROMPT,
                build_tool_plan_prompt(code, enabled, syntax_result, strict=strict),
            )
            payload = extract_json_object(response)
            raw_tools = payload.get("tools", [])
            if not isinstance(raw_tools, list):
                raise ValueError("tools 字段不是列表。")
            selected = [str(name) for name in raw_tools if str(name) in enabled]
            if len(selected) != len(set(selected)):
                selected = list(dict.fromkeys(selected))
            reason = str(payload.get("reason") or "根据代码特征选择了静态工具。")
            return selected, reason
        except Exception:
            return enabled, "模型规划失败，已自动回退到用户选定的静态工具。"

    def _run_tool(
        self,
        tool_name: str,
        code: str,
        steps: list[AgentStep],
        on_step: Callable[[AgentStep], None] | None,
    ) -> ToolResult:
        started = perf_counter()
        try:
            tool = self._registry.get(tool_name)
            result = tool.analyze(code)
        except Exception as exc:
            result = ToolResult(
                tool_name=tool_name,
                success=False,
                duration_ms=int((perf_counter() - started) * 1000),
                error=f"工具执行失败: {exc}",
            )

        status: Literal["completed", "failed"]
        if result.success:
            detail = f"{tool_name} 完成，发现 {len(result.findings)} 个问题。"
            status = "completed"
        else:
            detail = f"{tool_name} 执行失败：{result.error or '未知错误'}"
            status = "failed"

        self._emit(
            steps,
            AgentStep(title=f"调用工具：{tool_name}", detail=detail, status=status),
            on_step,
        )
        return result

    def _synthesize_report(
        self,
        code: str,
        tool_results: list[ToolResult],
        *,
        requirement: str | None,
        strict: bool,
    ) -> tuple[ReviewReport, str, Literal["completed", "failed", "skipped"]]:
        if not self._llm.available:
            return (
                self._fallback_report(
                    tool_results, "DeepSeek API Key 未配置，当前仅展示静态工具结果。"
                ),
                "LLM 未配置，已使用工具结果生成降级报告。",
                "skipped",
            )

        try:
            response = self._llm.complete(
                REVIEW_SYSTEM_PROMPT,
                build_review_prompt(code, tool_results, requirement=requirement, strict=strict),
            )
            report = ReviewReport.model_validate(extract_json_object(response))
            report = self._normalize_llm_evidence(report, tool_results)
            report = self._merge_tool_findings(report, tool_results)
            failed_tools = [result.tool_name for result in tool_results if not result.success]
            if failed_tools:
                report.limitations.append(
                    f"以下工具未能完成检查：{', '.join(failed_tools)}。报告可能不完整。"
                )
            return report, "LLM 已结合工具结果生成结构化报告。", "completed"
        except Exception as exc:
            return (
                self._fallback_report(
                    tool_results,
                    f"LLM 报告生成失败，当前使用工具结果降级展示：{exc}",
                ),
                "LLM 报告生成失败，已降级为工具结果报告。",
                "failed",
            )

    @staticmethod
    def _severity_rank(severity: Severity) -> int:
        return {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2}[severity]

    @staticmethod
    def _normalize_llm_evidence(
        report: ReviewReport,
        tool_results: list[ToolResult],
    ) -> ReviewReport:
        """Keep only evidence that is actually supported by a tool result for that line."""
        evidence_by_line: dict[int, set[str]] = {}
        for result in tool_results:
            for finding in result.findings:
                if finding.line is None:
                    continue
                evidence_by_line.setdefault(finding.line, set()).update(finding.evidence)

        for issue in report.issues:
            supported = [
                item
                for item in issue.evidence
                if issue.line is not None and item in evidence_by_line.get(issue.line, set())
            ]
            issue.evidence = supported or ["LLM 分析"]
        return report

    def _merge_tool_findings(
        self,
        report: ReviewReport,
        tool_results: list[ToolResult],
    ) -> ReviewReport:
        existing_by_location = {
            (issue.line, issue.category): issue
            for issue in report.issues
            if issue.line is not None
        }
        existing_titles = {
            (issue.line, issue.title.strip().casefold()) for issue in report.issues
        }
        for result in tool_results:
            if not result.success:
                continue
            for finding in result.findings:
                location_key = (
                    (finding.line, finding.category) if finding.line is not None else None
                )
                title_key = (finding.line, finding.title.strip().casefold())
                existing_issue = (
                    existing_by_location.get(location_key) if location_key is not None else None
                )
                if existing_issue is not None:
                    for evidence in finding.evidence:
                        if evidence not in existing_issue.evidence:
                            existing_issue.evidence.append(evidence)
                    if self._severity_rank(finding.severity) > self._severity_rank(
                        existing_issue.severity
                    ):
                        existing_issue.severity = finding.severity
                    continue
                if title_key in existing_titles:
                    continue

                issue = Issue.model_validate(finding.model_dump())
                report.issues.append(issue)
                if finding.title not in report.recommended_order:
                    report.recommended_order.append(finding.title)
                existing_titles.add(title_key)
                if issue.line is not None:
                    existing_by_location[(issue.line, issue.category)] = issue
        return report

    def _fallback_report(self, tool_results: list[ToolResult], limitation: str) -> ReviewReport:
        findings = self._deduplicate_findings(
            finding
            for result in tool_results
            if result.success
            for finding in result.findings
        )
        issues = [Issue.model_validate(finding.model_dump()) for finding in findings]
        recommended_order = [issue.title for issue in issues]
        if issues:
            summary = f"静态工具共发现 {len(issues)} 个明确问题，请优先处理严重程度较高的项目。"
        else:
            summary = "静态工具未发现明确问题，但这不代表代码在所有输入下都一定正确。"

        return ReviewReport(
            summary=summary,
            issues=issues,
            recommended_order=recommended_order,
            limitations=[limitation],
        )

    @staticmethod
    def _deduplicate_findings(findings: Iterable[IssueCandidate]) -> list[IssueCandidate]:
        unique: list[IssueCandidate] = []
        seen: set[tuple[int | None, str]] = set()
        for finding in findings:
            key = (finding.line, finding.title.strip().casefold())
            if key in seen:
                continue
            seen.add(key)
            unique.append(finding)
        return unique

    @staticmethod
    def _emit(
        steps: list[AgentStep],
        step: AgentStep,
        callback: Callable[[AgentStep], None] | None,
    ) -> None:
        steps.append(step)
        if callback is not None:
            callback(step)


