"""Formatting helpers for review reports."""

from __future__ import annotations

from reviewflow.models import Issue, ReviewReport, Severity

SEVERITY_LABELS = {
    Severity.HIGH: "高",
    Severity.MEDIUM: "中",
    Severity.LOW: "低",
}

CATEGORY_LABELS = {
    "syntax": "语法",
    "style": "规范",
    "logic": "逻辑",
    "type": "类型",
    "edge_case": "边界情况",
    "maintainability": "可维护性",
    "security": "安全",
}


def _issue_heading(issue: Issue) -> str:
    line_text = f"第 {issue.line} 行" if issue.line else "行号未知"
    severity = SEVERITY_LABELS.get(issue.severity, issue.severity.value)
    category = CATEGORY_LABELS.get(issue.category.value, issue.category.value)
    return f"[{severity}] {issue.title}（{category}，{line_text}）"


def report_to_markdown(report: ReviewReport) -> str:
    """Render a structured report as a downloadable Markdown document."""
    counts = report.counts
    lines = [
        "# reviewflow 静态代码审查报告",
        "",
        "## 总览",
        "",
        report.summary,
        "",
        f"- 高严重度：{counts['high']}",
        f"- 中严重度：{counts['medium']}",
        f"- 低严重度：{counts['low']}",
        "",
        "> 本报告基于静态分析，不会执行或运行被审查的代码。",
        "",
        "## 问题列表",
        "",
    ]

    if not report.issues:
        lines.append("未发现明确问题。")
    for index, issue in enumerate(report.issues, start=1):
        lines.extend(
            [
                f"### {index}. {_issue_heading(issue)}",
                "",
                f"**原因：** {issue.explanation}",
                "",
                f"**建议：** {issue.suggestion or '请结合上下文进一步检查。'}",
                "",
            ]
        )
        if issue.evidence:
            lines.extend([f"**证据：** {', '.join(issue.evidence)}", ""])
        if issue.example:
            lines.extend(["**示例：**", "", "```python", issue.example, "```", ""])

    if report.recommended_order:
        lines.extend(["## 建议修复顺序", ""])
        lines.extend(f"{index}. {title}" for index, title in enumerate(report.recommended_order, 1))
        lines.append("")

    if report.limitations:
        lines.extend(["## 限制说明", ""])
        lines.extend(f"- {limitation}" for limitation in report.limitations)
        lines.append("")

    return "\n".join(lines).strip() + "\n"
