"""Shared data models for the ReviewFlow multi-agent workflow."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IssueCategory(StrEnum):
    SYNTAX = "syntax"
    STYLE = "style"
    LOGIC = "logic"
    TYPE = "type"
    EDGE_CASE = "edge_case"
    MAINTAINABILITY = "maintainability"
    SECURITY = "security"


class InputMode(StrEnum):
    REQUIREMENT = "requirement"
    EXISTING_CODE = "existing_code"


class WorkflowStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    MAX_ITERATIONS_REACHED = "max_iterations_reached"
    FAILED = "failed"


class NodeStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentName(StrEnum):
    ORCHESTRATOR = "orchestrator"
    CODING_AGENT = "coding_agent"
    REVIEW_AGENT = "review_agent"
    FIX_AGENT = "fix_agent"
    USER = "user"


class MessageKind(StrEnum):
    WORKFLOW_STARTED = "workflow_started"
    CODE_READY = "code_ready"
    REVIEW_COMPLETED = "review_completed"
    FIX_COMPLETED = "fix_completed"
    WORKFLOW_FINISHED = "workflow_finished"
    FAILED = "failed"


class IssueCandidate(BaseModel):
    """An issue discovered by a static tool or by the review LLM."""

    model_config = ConfigDict(extra="ignore")

    severity: Severity = Severity.MEDIUM
    category: IssueCategory = IssueCategory.LOGIC
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    title: str
    explanation: str
    suggestion: str = ""
    example: str | None = None
    evidence: list[str] = Field(default_factory=list)

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, value: object) -> object:
        aliases = {
            "高": "high",
            "严重": "high",
            "中": "medium",
            "警告": "medium",
            "低": "low",
            "提示": "low",
        }
        if isinstance(value, str):
            return aliases.get(value.strip().lower(), value.strip().lower())
        return value

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, value: object) -> object:
        aliases = {
            "语法": "syntax",
            "规范": "style",
            "风格": "style",
            "逻辑": "logic",
            "类型": "type",
            "边界": "edge_case",
            "边界情况": "edge_case",
            "可维护性": "maintainability",
            "安全": "security",
        }
        if isinstance(value, str):
            return aliases.get(value.strip().lower(), value.strip().lower())
        return value


class Issue(IssueCandidate):
    """A final review issue shown to the user."""

    id: str = Field(default_factory=lambda: uuid4().hex[:8])


class ToolResult(BaseModel):
    """Normalized output from one static analysis tool."""

    model_config = ConfigDict(extra="ignore")

    tool_name: str
    success: bool
    duration_ms: int = 0
    findings: list[IssueCandidate] = Field(default_factory=list)
    raw_output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentStep(BaseModel):
    """One visible step inside a review-agent run."""

    title: str
    detail: str
    status: Literal["completed", "failed", "skipped"] = "completed"


class ReviewReport(BaseModel):
    """Structured final report returned by the review agent."""

    model_config = ConfigDict(extra="ignore")

    summary: str
    issues: list[Issue] = Field(default_factory=list)
    recommended_order: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        counts = {"high": 0, "medium": 0, "low": 0}
        for issue in self.issues:
            counts[issue.severity.value] += 1
        return counts


class StaticReviewResult(BaseModel):
    """Everything produced by one static review pass."""

    report: ReviewReport
    steps: list[AgentStep] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    plan_reason: str = ""


class WorkflowInput(BaseModel):
    """User input accepted by the workflow."""

    model_config = ConfigDict(extra="forbid")

    mode: InputMode = InputMode.REQUIREMENT
    requirement: str = ""
    initial_code: str = ""
    repair_threshold: Severity = Severity.MEDIUM
    max_repair_attempts: int = Field(default=2, ge=0, le=3)
    enable_ruff: bool = True
    enable_mypy: bool = True
    strict: bool = False

    @field_validator("repair_threshold")
    @classmethod
    def validate_threshold(cls, value: Severity) -> Severity:
        if value == Severity.LOW:
            raise ValueError("修复阈值只能是 high 或 medium。")
        return value

    @model_validator(mode="after")
    def validate_entry(self) -> WorkflowInput:
        if self.mode == InputMode.REQUIREMENT and not self.requirement.strip():
            raise ValueError("需求模式下必须填写需求描述。")
        if self.mode == InputMode.EXISTING_CODE and not self.initial_code.strip():
            raise ValueError("已有代码模式下必须填写 Python 代码。")
        return self


class CodeArtifact(BaseModel):
    """Structured output produced by the coding agent."""

    summary: str
    code: str
    assumptions: list[str] = Field(default_factory=list)


class ReviewArtifact(BaseModel):
    """Structured output produced by the review agent."""

    review_round: int = Field(ge=1)
    report: ReviewReport
    passed: bool
    blocking_issues: list[Issue] = Field(default_factory=list)
    steps: list[AgentStep] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    plan_reason: str = ""
    used_requirement: bool = False


class FixArtifact(BaseModel):
    """Structured output produced by the fixing agent."""

    summary: str
    code: str
    changes: list[str] = Field(default_factory=list)
    addressed_issue_ids: list[str] = Field(default_factory=list)


class AgentMessage(BaseModel):
    """One explicit message passed between workflow nodes."""

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    sender: AgentName
    recipient: AgentName
    kind: MessageKind
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class NodeOutput(BaseModel):
    """Uniform result returned by every workflow node."""

    sender: AgentName
    recipient: AgentName
    kind: MessageKind
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: NodeStatus = NodeStatus.COMPLETED
    duration_ms: int = 0


class WorkflowEvent(BaseModel):
    """One observable event in the workflow timeline."""

    sequence: int = Field(ge=1)
    node: AgentName
    status: NodeStatus
    summary: str
    detail: str = ""
    duration_ms: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class WorkflowState(BaseModel):
    """Mutable state shared by all workflow nodes."""

    run_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    input: WorkflowInput
    status: WorkflowStatus = WorkflowStatus.PENDING
    current_code: str = ""
    code_artifact: CodeArtifact | None = None
    current_review: ReviewArtifact | None = None
    review_history: list[ReviewArtifact] = Field(default_factory=list)
    fix_history: list[FixArtifact] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)
    events: list[WorkflowEvent] = Field(default_factory=list)
    repair_attempts: int = Field(default=0, ge=0)
    failure_reason: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None


class WorkflowResult(BaseModel):
    """Final result returned by the orchestrator."""

    run_id: str
    status: WorkflowStatus
    input: WorkflowInput
    final_code: str
    final_review: ReviewArtifact | None = None
    code_artifact: CodeArtifact | None = None
    review_history: list[ReviewArtifact] = Field(default_factory=list)
    fix_history: list[FixArtifact] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)
    events: list[WorkflowEvent] = Field(default_factory=list)
    repair_attempts: int = 0
    failure_reason: str | None = None
    started_at: datetime
    finished_at: datetime

    @property
    def completed(self) -> bool:
        return self.status in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.MAX_ITERATIONS_REACHED,
        }
