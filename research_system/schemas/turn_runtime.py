from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.core.id_utils import generate_ulid
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.state_machine import RunStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


TurnTaskType = Literal["regular", "review", "compact"]
TurnExecutionMode = Literal["core", "task", "graph"]
# The transport envelope already contains the complete canonical input. Keep a
# domain name for the Turn layer without introducing a second wire schema.
TurnInput = RequestEnvelope


class GraphExecutorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    graph_id: str | None = None
    initial_graph_ref: str | None = None
    planner_role: str = "main_agent.plan_agent"
    graph_policy_ref: str | None = None
    max_concurrency: int | None = Field(default=None, ge=1)
    max_graph_patches: int | None = Field(default=None, ge=0)


class HookPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_stop_continuations: int = Field(default=3, ge=0)
    timeout_seconds: int = Field(default=30, ge=1)
    failure_mode: Literal["fail_open", "fail_closed"] = "fail_closed"
    allow_prompt_continuation: bool = True
    allow_input_rewrite: bool = False


class HookConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    policy: HookPolicy = Field(default_factory=HookPolicy)
    plugin_refs: list[str] = Field(default_factory=list)


class FollowUpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    allow_model_follow_up: bool = True
    allow_pending_input: bool = True
    allow_child_followup_task: bool = True
    max_follow_up_count: int = Field(default=32, ge=0)
    mailbox_delivery_policy: Literal["current_turn", "next_turn", "governed"] = "governed"


class ChildTurnConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    max_children: int = Field(default=16, ge=0)


class ApprovalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    required: bool = False


class CompactConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    context_threshold: int = Field(default=0, ge=0)


class TaskSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_type: TurnTaskType = "regular"
    execution_mode: TurnExecutionMode = "core"
    reason_codes: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    graph_required: bool = False
    requires_persistence: bool = False
    requires_approval: bool = False


class PendingInputItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_id: str = Field(default_factory=generate_ulid)
    source: Literal["user", "system", "child", "hook", "tool"]
    kind: Literal["user_input", "inter_agent_communication", "context", "hook_prompt"]
    text: str
    target_turn_id: str | None = None
    trigger_turn: bool = False
    operation_id: str = Field(default_factory=generate_ulid)
    delivery_phase: Literal["current_turn", "next_turn"] = "current_turn"
    created_at: datetime = Field(default_factory=utc_now)


class TurnRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_turn_id: str | None = None
    parent_run_id: str
    parent_node_id: str | None = None
    relation: Literal["child", "review", "continuation", "compact"]


class ChildTurnSnapshot(BaseModel):
    """A child execution projected into its parent's canonical Turn."""

    model_config = ConfigDict(extra="forbid")

    child_turn_id: str = Field(default_factory=generate_ulid)
    child_thread_id: str = Field(default_factory=generate_ulid)
    parent_turn_id: str
    parent_run_id: str
    parent_node_id: str | None = None
    role_id: str
    relation: Literal["child", "review", "continuation", "compact"] = "child"
    status: Literal[
        "ready",
        "running",
        "paused",
        "waiting_approval",
        "completed",
        "completed_partial",
        "gated",
        "failed",
        "cancelled",
        "timed_out",
    ] = "ready"
    objective: str | None = None
    # Snapshot the effective contract so a ChildTurn can be recovered even
    # when the process-local role registry is rebuilt after a restart.
    role_contract: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    continuation: dict[str, Any] | None = None
    sequence: int = Field(default=0, ge=0)
    last_event_id: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class FollowupTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_turn_id: str
    target_child_turn_id: str
    target_agent_id: str
    message: str
    trigger_turn: bool = True
    operation_id: str = Field(default_factory=generate_ulid)


class FollowupTaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_child_turn_id: str
    accepted: bool
    delivery: Literal["queued", "delivered_current_turn", "triggered_new_turn", "rejected"]
    target_turn_id: str | None = None
    duplicate: bool = False
    reason: str | None = None


class TaskSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: TurnTaskType = "regular"
    execution_mode: TurnExecutionMode = "core"
    active_turn_id: str | None = None
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    pending_inputs: list[PendingInputItem] = Field(default_factory=list)
    # Operation receipts survive mailbox delivery.  Without this ledger a
    # retry that arrives after delivery can enqueue the same follow-up again.
    operation_receipts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    follow_up_count: int = Field(default=0, ge=0)
    pending_approval_ids: list[str] = Field(default_factory=list)
    stop_hook_active: bool = False
    hook_iteration: int = Field(default=0, ge=0)
    last_assistant_message: str | None = None
    continuation: dict[str, Any] | None = None
    compact_before_continue: bool = False
    graph_state_ref: str | None = None
    subagent_stop_node_ids: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class GraphExecutionState(BaseModel):
    """Serializable coordination state for one Graph step in a Turn."""

    model_config = ConfigDict(extra="forbid")

    graph_id: str
    revision: int = Field(default=1, ge=1)
    nodes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    frontier: list[str] = Field(default_factory=list)
    in_flight: dict[str, dict[str, Any]] = Field(default_factory=dict)
    pending_feedback: list[dict[str, Any]] = Field(default_factory=list)
    pending_patches: list[dict[str, Any]] = Field(default_factory=list)
    gates: dict[str, dict[str, Any]] = Field(default_factory=dict)


class TurnConfig(BaseModel):
    """Immutable configuration selected for one root Turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    config_version: int = Field(default=1, ge=1)
    task_type: TurnTaskType = "regular"
    execution_mode: TurnExecutionMode = "core"
    task_policy_ref: str | None = None
    graph_config: GraphExecutorConfig | None = None
    hook_config: HookConfig | None = None
    follow_up_config: FollowUpConfig | None = None
    child_turn_config: ChildTurnConfig | None = None
    approval_config: ApprovalConfig | None = None
    compact_config: CompactConfig | None = None
    plugin_refs: list[str] = Field(default_factory=list)
    selection: TaskSelection = Field(default_factory=TaskSelection)
    timeout_seconds: float | None = Field(default=None, gt=0)


class TurnSnapshot(BaseModel):
    """Canonical root Turn checkpoint shared by Core and Graph execution."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(default_factory=generate_ulid)
    run_id: str
    thread_id: str
    request_id: str
    user_id: str | None = None
    tenant_id: str | None = None
    parent_turn_id: str | None = None
    relation: Literal["child", "review", "continuation", "compact"] | None = None
    task_type: TurnTaskType = "regular"
    execution_mode: TurnExecutionMode = "core"
    status: RunStatus = "received"
    config: TurnConfig = Field(default_factory=TurnConfig)
    config_hash: str = ""
    task_selection: TaskSelection = Field(default_factory=TaskSelection)
    child_turns: list[ChildTurnSnapshot] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    sequence: int = Field(default=0, ge=0)
    task_state_ref: str | None = None
    graph_state_ref: str | None = None
    last_event_id: str | None = None
    error: str | None = None
    # The deadline belongs to the root Turn, not to one worker invocation.
    # Persisting wall-clock time lets recovery preserve the original budget.
    deadline_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TaskOutcome(BaseModel):
    """Normalized result returned by a RegularTask step executor."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    execution_mode: TurnExecutionMode
    status: str
    result: Any
    graph_state_ref: str | None = None
    continuation: dict[str, Any] | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    final_artifact_ids: list[str] = Field(default_factory=list)
    pending_approval_ids: list[str] = Field(default_factory=list)
    child_turn_ids: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)
    error: str | None = None


__all__ = [
    "TaskOutcome",
    "ApprovalConfig",
    "ChildTurnConfig",
    "ChildTurnSnapshot",
    "CompactConfig",
    "FollowUpConfig",
    "FollowupTaskRequest",
    "FollowupTaskResult",
    "GraphExecutorConfig",
    "GraphExecutionState",
    "HookConfig",
    "HookPolicy",
    "PendingInputItem",
    "TaskSelection",
    "TaskSnapshot",
    "TurnRelation",
    "TurnConfig",
    "TurnExecutionMode",
    "TurnInput",
    "TurnSnapshot",
    "TurnTaskType",
]
