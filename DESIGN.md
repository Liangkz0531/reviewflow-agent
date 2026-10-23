# ReviewFlow 设计文档

## 1. 设计目标

ReviewFlow 的目标不是把所有功能塞进一个大 Agent，而是把 Homework 1 的静态代码审查能力放入一个可观察、可扩展的多 Agent Workflow。实现重点是：

- 至少两个 Agent 协作；本项目使用 Coding、Review、Fix 三个 Agent。
- Agent 间通过结构化消息与共享状态通信。
- 支持顺序执行、条件分支和有限循环。
- Agent、工具和编排器职责分离，便于新增节点。
- 全流程只做静态分析，不执行任何输入或生成代码。

## 2. 非目标

- 不执行代码、不运行 pytest、不启动生成程序。
- 不处理多文件项目、依赖树或跨文件调用关系。
- 不自动覆盖用户磁盘上的源文件。
- 不实现复杂调度器、分布式执行或数据库持久化。
- 不依赖 LangGraph、CrewAI、AutoGen 等框架。

## 3. 总体架构

```text
Streamlit UI
    |
    v
WorkflowOrchestrator
    |-- CodingAgent ---- CodeArtifact
    |-- ReviewAgent ---- StaticReviewAgent ---- AST / Ruff / Mypy / LLM
    `-- FixAgent ------- FixArtifact
            |
            v
   WorkflowState / AgentMessage / WorkflowEvent
```

`WorkflowOrchestrator` 只负责流程控制，不知道 Agent 内部的 Prompt 或工具细节。每个节点实现统一的 `WorkflowNode` 协议：

```python
class WorkflowNode(Protocol):
    name: AgentName

    def run(self, state: WorkflowState) -> NodeOutput:
        ...
```

节点不直接调用其他节点。它读取 `WorkflowState`，返回 `NodeOutput`；编排器负责更新状态、记录事件并决定下一节点。

## 4. 状态机

```text
PENDING
   |
   v
RUNNING
   |-- 需求模式 -> Coding -> Review
   |-- 已有代码 -> 语法验证 -> Review
                         |
              Review.passed == true
                         |----> COMPLETED
                         |
              Review.passed == false
                         |
             repair_attempts >= max
                         |----> MAX_ITERATIONS_REACHED
                         |
                    Fix -> Review
```

任何节点返回失败、输入语法无效或出现未处理异常时，状态转为 `FAILED`。达到最大修复次数不是系统错误，而是可解释的终止状态。

## 5. 数据契约

### WorkflowInput

- `mode`：`REQUIREMENT` 或 `EXISTING_CODE`。
- `requirement`、`initial_code`：入口内容。
- `repair_threshold`：`medium` 表示 high/medium 阻断，`high` 表示只有 high 阻断。
- `max_repair_attempts`：0～3，默认 2。
- `enable_ruff`、`enable_mypy`、`strict`：静态工具和 Prompt 配置。

### WorkflowState

- `current_code`：当前通过语法验证的有效版本。
- `code_artifact`：编码 Agent 的输出。
- `current_review`、`review_history`：当前和各轮审查。
- `fix_history`：每次修复的完整文件与修改说明。
- `messages`：Agent 发往编排器或其他节点的结构化消息。
- `events`：按顺序记录节点状态、摘要、详情和耗时。
- `repair_attempts`、`status`、`failure_reason`：终止判断。

### NodeOutput

所有节点统一返回：

```json
{
  "sender": "review_agent",
  "recipient": "orchestrator",
  "kind": "review_completed",
  "summary": "第 1 轮审查：发现 2 个阻断问题。",
  "payload": {"review_round": 1},
  "status": "completed",
  "duration_ms": 1200
}
```

`payload` 会转换为对应工件模型：`CodeArtifact`、`ReviewArtifact` 或 `FixArtifact`。无效 payload 会使该节点结果失败，而不会污染共享状态。

## 6. 消息流

```text
User -> Orchestrator: WorkflowInput
Orchestrator -> CodingAgent: WorkflowState
CodingAgent -> Orchestrator: code_ready
Orchestrator -> ReviewAgent: WorkflowState
ReviewAgent -> Orchestrator: review_completed
Orchestrator -> FixAgent: WorkflowState
FixAgent -> Orchestrator: fix_completed
Orchestrator -> ReviewAgent: WorkflowState
Orchestrator -> User: workflow_finished / failed
```

消息同时保存在 `WorkflowState.messages` 中，因此用户界面和测试都能看到完整通信记录。

## 7. 审查与修复策略

- AST 先做语法和轻量语义检查。
- 语法正确时，LLM 根据代码选择 Ruff/Mypy；规划失败则运行启用工具。
- LLM 综合工具结果生成结构化报告。
- Ruff 或 Mypy 单项失败不终止 Workflow，而是附加限制说明。
- 工具直接发现的问题会与 LLM 报告合并，避免遗漏高置信问题。
- ReviewAgent 按阈值计算 `blocking_issues`。
- FixAgent 接收当前代码、阻断问题和历史修复记录，输出完整替换文件。
- 修复代码重新进入审查节点，而不是被直接信任。

## 8. 错误处理与恢复

- LLM 请求：沿用 Homework 1 的超时、指数退避和三次尝试。
- 结构化输出：第一次验证失败后，一次要求模型修复 JSON。
- 代码语法：Coding/Fix 输出第一次语法错误后，一次携带错误信息重新生成。
- 已有代码语法错误：在调用 LLM 前停止。
- API Key 缺失：需求模式失败；已有代码可使用静态工具降级报告。
- 节点失败：保留最后一个有效 `current_code`，记录 `failure_reason`，不继续路由。
- UI 回调异常：不改变 Workflow 结果。

## 9. 扩展新 Agent

以新增 Security Agent 为例：

1. 定义 `SecurityArtifact` 和对应 Prompt。
2. 实现 `WorkflowNode`，读取 `state.current_code`，返回 `NodeOutput`。
3. 在 `MessageKind` 和 `AgentName` 中增加类型。
4. 在编排器中增加条件边，例如高风险问题先进入 Security Agent。
5. 在 UI 中增加状态和结果展示。
6. 为新增条件和失败分支添加 Fake Node 测试。

现有 Coding、Review、Fix Agent 不需要修改内部实现。

## 10. 安全设计

- 系统 Prompt 明确把需求、代码、审查文本视为不可信数据。
- 不向 Agent 提供文件系统、Shell 或网络工具。
- 生成代码只进入内存、Streamlit 和导出文件，不写入项目源码。
- Ruff/Mypy 运行在临时副本上，超时后终止。
- `.env`、虚拟环境和 API Key 不进入 Git。
- Markdown/JSON 导出不包含环境变量或 API Key。

## 11. 测试策略

- Fake Node 验证状态转换、条件分支、循环上限和消息记录。
- Fake LLM 验证 JSON 修复、语法修复、阈值判断和失败处理。
- 真实 AST/Ruff/Mypy 用于静态工具集成测试。
- Streamlit 使用 AppTest 或手工冒烟验证页面导入和基本交互。
- 验收命令：`pytest`、`ruff check .`、`mypy src`。
