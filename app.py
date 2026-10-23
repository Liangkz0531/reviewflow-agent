"""Streamlit entry point for the ReviewFlow multi-agent workflow."""

from __future__ import annotations

import json

import streamlit as st
from pydantic import ValidationError

from reviewflow.config import get_settings
from reviewflow.factory import build_orchestrator
from reviewflow.models import (
    InputMode,
    Issue,
    ReviewArtifact,
    Severity,
    WorkflowEvent,
    WorkflowInput,
    WorkflowResult,
    WorkflowStatus,
)
from reviewflow.services.report import CATEGORY_LABELS, SEVERITY_LABELS
from reviewflow.services.workflow_report import code_diff, workflow_result_to_markdown

st.set_page_config(
    page_title="ReviewFlow 多 Agent 工作流",
    page_icon="🔄",
    layout="wide",
    initial_sidebar_state="expanded",
)


STATUS_LABELS = {
    WorkflowStatus.COMPLETED: "✅ 已完成，通过审查",
    WorkflowStatus.MAX_ITERATIONS_REACHED: "⚠️ 达到最大修复次数",
    WorkflowStatus.FAILED: "❌ 执行失败",
    WorkflowStatus.RUNNING: "⏳ 运行中",
    WorkflowStatus.PENDING: "等待运行",
}
THRESHOLD_LABELS = {
    "高中问题触发修复": Severity.MEDIUM,
    "仅高问题触发修复": Severity.HIGH,
}


@st.cache_resource
def get_workflow():
    """Build and cache the default workflow for this Streamlit process."""
    settings = get_settings()
    return build_orchestrator(settings)


def render_workflow_graph() -> None:
    """Show the fixed graph that the orchestrator executes."""
    left, middle, right = st.columns(3)
    left.info("**1. Coding Agent**\n\n需求 → 结构化 Python 文件")
    middle.info("**2. Review Agent**\n\nAST + Ruff + Mypy + LLM 复审")
    right.info("**3. Fix Agent**\n\n阻断问题 → 完整修复文件")
    st.caption(
        "编排器控制顺序与条件分支：审查通过则结束；存在达到阈值的问题且未超过修复次数时，"
        "交给 Fix Agent，然后重新审查。"
    )


def render_issue(issue: Issue) -> None:
    """Render one structured review issue."""
    severity = SEVERITY_LABELS.get(issue.severity, issue.severity.value)
    category = CATEGORY_LABELS.get(issue.category.value, issue.category.value)
    location = f"第 {issue.line} 行" if issue.line else "行号未知"
    with st.expander(f"[{severity}] {issue.title} · {category} · {location}", expanded=False):
        st.markdown(f"**原因：** {issue.explanation}")
        st.markdown(f"**建议：** {issue.suggestion or '请结合上下文进一步检查。'}")
        if issue.evidence:
            st.caption("证据：" + "、".join(issue.evidence))
        if issue.example:
            st.code(issue.example, language="python")

def render_review(review: ReviewArtifact) -> None:
    """Render one review round and its static tool trace."""
    counts = review.report.counts
    left, middle, right, verdict = st.columns(4)
    left.metric("高严重度", counts["high"])
    middle.metric("中严重度", counts["medium"])
    right.metric("低严重度", counts["low"])
    verdict.metric("阻断问题", len(review.blocking_issues))
    st.write(review.report.summary)
    if review.used_requirement:
        st.caption("本轮审查同时对照了原始需求。")
    if not review.report.issues:
        st.success("本轮未发现明确问题。")
    for issue in review.report.issues:
        render_issue(issue)
    if review.report.recommended_order:
        st.markdown("**建议修复顺序：**")
        for index, title in enumerate(review.report.recommended_order, start=1):
            st.write(f"{index}. {title}")
    with st.expander("静态工具与审查步骤", expanded=False):
        for step in review.steps:
            st.write(f"- **{step.title}**：{step.detail}（{step.status}）")
        for tool_result in review.tool_results:
            label = f"{tool_result.tool_name} · {'成功' if tool_result.success else '失败'}"
            with st.expander(label, expanded=False):
                st.json(tool_result.model_dump(mode="json"))
    if review.report.limitations:
        st.warning("；".join(review.report.limitations))


def render_event(event: WorkflowEvent) -> str:
    """Return a compact timeline row for one workflow event."""
    icon = {
        "completed": "✅",
        "failed": "❌",
        "skipped": "⏭️",
    }.get(event.status.value, "•")
    detail = f" — {event.detail}" if event.detail else ""
    duration = f" `{event.duration_ms} ms`" if event.duration_ms else ""
    return f"{event.sequence}. {icon} **{event.node.value}**：{event.summary}{detail}{duration}"

def render_result(result: WorkflowResult) -> None:
    """Render the completed workflow state and all artifacts."""
    st.divider()
    st.subheader("Workflow 结果")
    status_col, review_col, fix_col, run_col = st.columns(4)
    status_col.metric("最终状态", STATUS_LABELS.get(result.status, result.status.value))
    review_col.metric("审查轮次", len(result.review_history))
    fix_col.metric("修复次数", result.repair_attempts)
    run_col.metric("事件数量", len(result.events))

    if result.failure_reason:
        st.error(result.failure_reason)
    elif result.status == WorkflowStatus.MAX_ITERATIONS_REACHED:
        st.warning("达到最大修复次数，以下保留最后一份有效代码和剩余问题。")
    else:
        st.success("Workflow 已到达终止条件。")

    st.markdown("### 最终代码")
    st.code(result.final_code or "# 没有生成或提交代码", language="python")
    report_markdown = workflow_result_to_markdown(result)
    json_text = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
    download_left, download_right = st.columns(2)
    download_left.download_button(
        "下载完整 Markdown 报告",
        data=report_markdown,
        file_name=f"reviewflow-{result.run_id}.md",
        mime="text/markdown",
        use_container_width=True,
    )
    download_right.download_button(
        "下载完整 JSON 运行记录",
        data=json_text,
        file_name=f"reviewflow-{result.run_id}.json",
        mime="application/json",
        use_container_width=True,
    )

    st.markdown("### 执行时间线")
    for event in result.events:
        st.markdown(render_event(event))

    st.markdown("### 最终审查")
    if result.final_review is None:
        st.info("没有可展示的审查结果。")
    else:
        render_review(result.final_review)

    if result.review_history:
        st.markdown("### 各轮审查汇总")
        for review in result.review_history:
            label = f"第 {review.review_round} 轮 · {'通过' if review.passed else '未通过'}"
            with st.expander(label, expanded=False):
                render_review(review)

    if result.fix_history:
        st.markdown("### 修复记录与差异")
        initial = result.code_artifact.code if result.code_artifact else result.input.initial_code
        versions = [initial]
        versions.extend(fix.code for fix in result.fix_history)
        for index, fix in enumerate(result.fix_history, start=1):
            with st.expander(f"第 {index} 次修复：{fix.summary}", expanded=False):
                for change in fix.changes:
                    st.write(f"- {change}")
                diff = code_diff(
                    versions[index - 1],
                    versions[index],
                    from_label=f"version-{index - 1}",
                    to_label=f"version-{index}",
                )
                st.code(diff or "# 修复前后内容一致", language="diff")

    with st.expander("共享状态快照", expanded=False):
        st.json(
            {
                "run_id": result.run_id,
                "status": result.status.value,
                "repair_attempts": result.repair_attempts,
                "review_rounds": len(result.review_history),
                "blocking_issues": (
                    len(result.final_review.blocking_issues) if result.final_review else 0
                ),
                "failure_reason": result.failure_reason,
                "started_at": result.started_at.isoformat(),
                "finished_at": result.finished_at.isoformat(),
            }
        )

    with st.expander("Agent 消息流", expanded=False):
        for message in result.messages:
            st.markdown(
                f"**{message.sender.value} → {message.recipient.value}** "
                f"（`{message.kind.value}`）  \n{message.summary}"
            )
            st.json(message.payload)

def decode_upload(uploaded_file) -> str:
    """Read one uploaded UTF-8 file without persisting it."""
    data = uploaded_file.getvalue()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("文件不是有效 UTF-8，请转换编码后重试。") from exc


def main() -> None:
    """Render the ReviewFlow page."""
    settings = get_settings()
    st.title("ReviewFlow 多 Agent 代码审查工作流")
    st.write(
        "输入自然语言需求或已有 Python 代码，由 Coding、Review、Fix 三个角色 Agent "
        "通过共享状态和结构化消息完成审查闭环。"
    )
    render_workflow_graph()

    with st.sidebar:
        st.header("Workflow 配置")
        if settings.api_key_configured:
            st.success("DeepSeek API Key 已配置")
        else:
            st.warning("尚未配置 DeepSeek API Key；需求生成和自动修复不可用。")
        mode_label = st.radio(
            "流程入口",
            ["自然语言需求", "已有 Python 代码"],
            index=0,
        )
        threshold_label = st.radio(
            "修复触发阈值",
            list(THRESHOLD_LABELS),
            index=0,
        )
        max_repair_attempts = st.slider(
            "最大修复次数",
            min_value=0,
            max_value=3,
            value=2,
            help="0 表示只审查一次，不自动修复。",
        )
        enable_ruff = st.toggle("启用 Ruff", value=True)
        enable_mypy = st.toggle("启用 Mypy", value=True)
        strict = st.toggle("严格审查模式", value=False)
        if st.button("清除本次运行结果", use_container_width=True):
            st.session_state.pop("workflow_result", None)
            st.rerun()

    requirement = ""
    initial_code = ""
    if mode_label == "自然语言需求":
        requirement = st.text_area(
            "需求描述",
            height=220,
            placeholder="例如：实现一个带输入校验、可处理空列表并返回统计结果的 Python 函数。",
        )
    else:
        uploaded_file = st.file_uploader("上传单个 UTF-8 Python 文件", type=["py"])
        if uploaded_file is not None:
            try:
                initial_code = decode_upload(uploaded_file)
            except ValueError as exc:
                st.error(str(exc))
        initial_code = st.text_area(
            "Python 代码",
            value=initial_code,
            height=420,
            placeholder="在此粘贴完整的单个 Python 文件。",
        )

    run_clicked = st.button("开始执行 Workflow", type="primary", use_container_width=True)
    if run_clicked:
        try:
            workflow_input = WorkflowInput(
                mode=(
                    InputMode.REQUIREMENT
                    if mode_label == "自然语言需求"
                    else InputMode.EXISTING_CODE
                ),
                requirement=requirement,
                initial_code=initial_code,
                repair_threshold=THRESHOLD_LABELS[threshold_label],
                max_repair_attempts=max_repair_attempts,
                enable_ruff=enable_ruff,
                enable_mypy=enable_mypy,
                strict=strict,
            )
        except ValidationError as exc:
            messages = [error["msg"] for error in exc.errors()]
            st.error("；".join(messages))
        else:
            with st.status("正在执行多 Agent Workflow...", expanded=True) as progress:
                def on_event(event: WorkflowEvent) -> None:
                    progress.markdown(render_event(event))

                try:
                    result = get_workflow().run(workflow_input, on_event=on_event)
                except Exception as exc:
                    progress.update(label="Workflow 执行失败", state="error", expanded=True)
                    st.exception(exc)
                else:
                    st.session_state["workflow_result"] = result
                    if result.status == WorkflowStatus.FAILED:
                        progress.update(label="Workflow 执行失败", state="error", expanded=True)
                    else:
                        progress.update(label="Workflow 执行完成", state="complete", expanded=False)
                    st.rerun()

    result = st.session_state.get("workflow_result")
    if isinstance(result, WorkflowResult):
        render_result(result)


if __name__ == "__main__":
    main()


