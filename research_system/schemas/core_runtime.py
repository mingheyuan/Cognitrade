from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_system.runtime.core.id_utils import generate_ulid
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.execution_graph import ExecutionGraphPatch
from research_system.schemas.state_machine import IntakeStatus, RunStatus, ToolCallStatus
from research_system.schemas.tools import ToolDefinition


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


RequestKind = Literal["conversation", "task", "domain_task"]
CoreRunStatus = RunStatus
PlanAction = Literal[
    "continue",
    "replan",
    "retry",
    "clarify",
    "stop",
    "wait_approval",
]


class PlanDecision(BaseModel):
    """The only state-changing decision the Main Agent planning role emits."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(default_factory=generate_ulid)
    action: PlanAction
    reason: str
    graph_patch: ExecutionGraphPatch | None = None
    target_node_ids: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    approval_ids: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    actor: str = "main_agent.plan_agent"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_decision(self) -> "PlanDecision":
        if not self.reason.strip():
            raise ValueError("PlanDecision reason is required")
        if self.actor != "main_agent.plan_agent":
            raise ValueError("only Main Agent PlanAgent role may issue PlanDecision")
        if self.action == "replan" and self.graph_patch is None:
            raise ValueError("replan decision requires graph_patch")
        if self.graph_patch is not None and self.graph_patch.actor != self.actor:
            raise ValueError("graph patch actor does not match PlanDecision actor")
        if self.action == "clarify" and not str(self.clarification_question or "").strip():
            raise ValueError("clarify decision requires clarification_question")
        if self.action == "retry" and not (self.target_node_ids or self.graph_patch):
            raise ValueError("retry decision requires target_node_ids or graph_patch")
        self.target_node_ids = list(dict.fromkeys(self.target_node_ids))
        self.approval_ids = list(dict.fromkeys(self.approval_ids))
        return self


class RequestIntakeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: IntakeStatus
    request_kind: RequestKind
    recommended_mode: Literal["core", "task", "graph"] = "core"
    intent: str | None = None
    domain_id: str | None = None
    candidate_domains: list[str] = Field(default_factory=list)
    candidate_capabilities: list[str] = Field(default_factory=list)
    slots: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    trace: dict[str, Any] = Field(default_factory=dict)

    def apply_to(self, request: RequestEnvelope) -> RequestEnvelope:
        """Apply the accepted intake result without dropping caller constraints."""
        if self.status != "accepted":
            raise ValueError("only an accepted RequestIntake decision can resolve a request")
        return request.model_copy(
            update={
                "intent": self.intent or request.intent,
                "slots": {**dict(request.slots), **dict(self.slots)},
                "constraints": {**dict(request.constraints), **dict(self.constraints)},
                "clarification_status": (
                    "resolved"
                    if request.clarification_status == "pending"
                    else request.clarification_status
                ),
            }
        )


class IntakeClassificationProposal(BaseModel):
    """Untrusted model proposal used only to improve RequestIntake recall."""

    model_config = ConfigDict(extra="forbid")

    request_kind: RequestKind = "conversation"
    intent: str | None = None
    domain_id: str | None = None
    candidate_domains: list[str] = Field(default_factory=list)
    candidate_capabilities: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None


class AgentToolCallRequest(BaseModel):
    """A model-issued Tool request inside one Main Agent turn."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = None


class AgentTurnResponse(BaseModel):
    """Provider-neutral response used by the finite Main Agent Tool loop."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["message", "tool_calls", "clarification", "replan"] = "message"
    message: str | None = None
    tool_calls: list[AgentToolCallRequest] = Field(default_factory=list)
    question: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


CoreToolDefinition = ToolDefinition


class CoreToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(default_factory=generate_ulid)
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    actor: str
    run_id: str
    thread_id: str
    request_id: str
    graph_revision: int | None = None
    node_id: str | None = None
    idempotency_key: str | None = None


class CoreToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool_name: str
    status: ToolCallStatus
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    retryable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime = Field(default_factory=utc_now)


class MainAgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    thread_id: str
    request_id: str
    status: CoreRunStatus = "running"
    mode: Literal["core", "task", "graph"] = "core"
    intent: str | None = None
    request_kind: RequestKind = "conversation"
    graph_id: str | None = None
    graph_revision: int | None = None
    turn_count: int = Field(default=0, ge=0)
    tool_loop_limit: int = Field(default=4, ge=1)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    tool_call_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    assistant_message: str | None = None
    clarification_question: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    last_event_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MainAgentRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    thread_id: str
    request_id: str
    status: CoreRunStatus
    mode: Literal["core", "task", "graph"]
    intent: str | None = None
    message: str
    clarification_question: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    tool_calls: list[CoreToolResult] = Field(default_factory=list)
    state: MainAgentState


__all__ = [
    "AgentToolCallRequest",
    "AgentTurnResponse",
    "CoreRunStatus",
    "CoreToolCall",
    "CoreToolDefinition",
    "CoreToolResult",
    "IntakeClassificationProposal",
    "IntakeStatus",
    "MainAgentRunResult",
    "MainAgentState",
    "PlanAction",
    "PlanDecision",
    "RequestIntakeDecision",
    "RequestKind",
]
