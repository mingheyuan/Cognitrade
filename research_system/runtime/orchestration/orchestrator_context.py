from __future__ import annotations

from dataclasses import dataclass

from research_system.runtime.core.blackboard import Blackboard
from research_system.runtime.core.control_plane import RunControlPlane
from research_system.runtime.core.event_stream import EventStream
from research_system.runtime.core.logger import RunLogger
from research_system.runtime.history.session_history import SessionHistory
from research_system.runtime.storage.checkpoint_store import CheckpointStore
from research_system.runtime.storage.run_store import RunPaths
from research_system.schemas.runtime import RunStateSnapshot


@dataclass
class RuntimeContext:
    run_id: str
    paths: RunPaths
    event_stream: EventStream
    blackboard: Blackboard
    checkpoint_store: CheckpointStore
    control_plane: RunControlPlane
    session_history: SessionHistory
    logger: RunLogger
    state: RunStateSnapshot
    subagent_trace: list[dict]
    dynamic_trace: list[dict]
    current_action_id: str | None = None
    current_node_id: str | None = None
    current_action_objective: str | None = None


def build_runtime_context(*, run_id: str, paths: RunPaths, state: RunStateSnapshot, run_timeout_seconds: int) -> RuntimeContext:
    context = RuntimeContext(
        run_id=run_id,
        paths=paths,
        event_stream=EventStream(paths.events),
        blackboard=Blackboard(paths.blackboard),
        checkpoint_store=CheckpointStore(paths.checkpoint),
        control_plane=RunControlPlane(paths.run_control),
        session_history=SessionHistory(paths.root),
        logger=RunLogger(paths.logs),
        state=state,
        subagent_trace=[],
        dynamic_trace=[],
    )
    context.control_plane.initialize(run_id=run_id, run_timeout_seconds=run_timeout_seconds)
    return context
