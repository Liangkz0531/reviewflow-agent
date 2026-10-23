"""Markdown and diff helpers for complete workflow runs."""

from __future__ import annotations

import difflib

from reviewflow.models import WorkflowResult, WorkflowStatus
from reviewflow.services.report import report_to_markdown

_STATUS_LABELS = {
    WorkflowStatus.COMPLETED: "已完成，通过审查",
    WorkflowStatus.MAX_ITERATIONS_REACHED: "达到最大修复次数",
    WorkflowStatus.FAILED: "执行失败",
    WorkflowStatus.RUNNING: "运行中",
    WorkflowStatus.PENDING: "等待运行",
}


def code_diff(
    before: str,
    after: str,
    *,
    from_label: str = "修复前",
    to_label: str = "修复后",
) -> str:
    """Return a unified diff between two source versions."""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=from_label,
            tofile=to_label,
        )
    )


def workflow_result_to_markdown(result: WorkflowResult) -> str:
    """Render a complete workflow trace, report and final source as Markdown."""
    mode = "需求生成" if result.input.mode.value == "requirement" else "已有代码"
    lines = [
        "# ReviewFlow 多 Agent 代码审查报告",
        "",
        f"- 运行编号：`{result.run_id}`",
        f"- 入口模式：{mode}",
        f"- 最终状态：{_STATUS_LABELS.get(result.status, result.status.value)}",
        f"- 审查轮次：{len(result.review_history)}",
        f"- 修复次数：{result.repair_attempts}",
        "",
        "## Workflow 事件时间线",
        "",
    ]
    for event in result.events:
        duration = f"（{event.duration_ms} ms）" if event.duration_ms else ""
        detail = f"：{event.detail}" if event.detail else ""
        icon = "✅" if event.status.value == "completed" else "❌"
        lines.append(
            f"{event.sequence}. {icon} **{event.node.value}** — {event.summary}{detail}{duration}"
        )
    lines.extend(["", "## Agent 消息", ""])
    for message in result.messages:
        lines.extend(
            [
                f"### {message.sender.value} → {message.recipient.value}",
                "",
                f"- 类型：`{message.kind.value}`",
                f"- 摘要：{message.summary}",
                "",
            ]
        )

    if result.code_artifact:
        lines.extend(
            [
                "## 编码 Agent 输出",
                "",
                result.code_artifact.summary,
                "",
            ]
        )
        if result.code_artifact.assumptions:
            lines.extend(
                f"- 假设：{item}" for item in result.code_artifact.assumptions
            )
            lines.append("")

    for review in result.review_history:
        lines.extend(
            [
                "",
                f"## 第 {review.review_round} 轮审查",
                "",
                report_to_markdown(review.report),
            ]
        )

    for index, fix in enumerate(result.fix_history, start=1):
        lines.extend(
            [
                "",
                f"## 第 {index} 次修复：{fix.summary}",
                "",
            ]
        )
        lines.extend(f"- {change}" for change in fix.changes)
        lines.append("")

    if result.final_code:
        lines.extend(
            [
                "## 最终代码",
                "",
                "```python",
                result.final_code,
                "```",
                "",
            ]
        )

    if result.failure_reason:
        lines.extend(["## 失败原因", "", result.failure_reason, ""])

    lines.extend(
        [
            "## 安全说明",
            "",
            "> ReviewFlow 只进行静态分析，不执行用户提交或 Agent 生成的代码。",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"

