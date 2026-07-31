from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from research_system.runtime.core.approvals import HumanApprovalStore
from research_system.runtime.follow_up import FollowUpRuntime
from research_system.runtime.hooks import HookRuntime
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.core_runtime import RequestIntakeDecision
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.subagents import RoleContract
from research_system.schemas.turn_runtime import (
    FollowupTaskRequest,
    FollowupTaskResult,
    TaskOutcome,
    TurnConfig,
    TurnSnapshot,
    TurnTaskType,
)


class TurnStepExecutor(Protocol):
    def execute(self, context: "TurnContext") -> TaskOutcome: ...


class TurnTask(Protocol):
    """Explicit lifecycle contract for a Task mounted in one root Turn."""

    task_type: TurnTaskType

    def start_or_resume(self, context: "TurnContext") -> TaskOutcome: ...

    def start(self, context: "TurnContext") -> TaskOutcome: ...

    def resume(self, context: "TurnContext") -> TaskOutcome: ...

    def cancel(self, context: "TurnContext", reason: str) -> TaskOutcome: ...


@dataclass
class TurnContext:
    request: RequestEnvelope
    decision: RequestIntakeDecision
    run_id: str
    thread_id: str
    turn: TurnSnapshot
    config: TurnConfig
    run_store: RunStore
    main_agent: Any
    event_callback: Callable[[dict[str, Any]], None] | None = None
    resume: bool = False
    graph: Any | None = None
    seed_artifacts: list[ArtifactDraft] | None = None
    rerun_from: str | None = None
    hook_runtime: HookRuntime | None = None
    follow_up_runtime: FollowUpRuntime | None = None
    followup_task_handler: Callable[[FollowupTaskRequest], FollowupTaskResult] | None = None
    core_session: Any | None = None
    storage_root: Path | None = None
    parent_turn_id: str | None = None
    child_role_id: str | None = None
    role_contract: RoleContract | None = None
    role_approval_id: str | None = None
    approval_store: HumanApprovalStore | None = None
    deadline_at: float | None = None
    last_result: Any | None = None
    recovery_continuation: bool = False
    worker_restart_recovery: bool = False

    @property
    def root(self) -> Path:
        return self.storage_root or self.run_store.load_run_paths(self.run_id).root


__all__ = ["TurnContext", "TurnStepExecutor", "TurnTask"]
