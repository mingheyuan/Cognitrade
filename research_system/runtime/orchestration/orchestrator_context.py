from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from research_system.runtime.core.blackboard import Blackboard
from research_system.runtime.core.control_plane import RunControlPlane
from research_system.runtime.core.event_stream import EventStream
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.core.logger import RunLogger
from research_system.runtime.core.agent_control import AgentControl
from research_system.runtime.history.session.session_history import SessionHistory
from research_system.runtime.core.execution_policy import RuntimeExecutionPolicy
from research_system.runtime.storage.jsonl import write_json
from research_system.runtime.storage.runs.checkpoint_store import CheckpointStore
from research_system.runtime.storage.runs.run_store import RunPaths
from research_system.schemas.runtime import RunStateSnapshot, RuntimeThreadState, RuntimeTurnState


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
    thread: RuntimeThreadState
    turn: RuntimeTurnState
    agent_control: AgentControl
    execution_policy: RuntimeExecutionPolicy
    subagent_trace: list[dict]
    dynamic_trace: list[dict]
    cache_restore_run_root: Path | None = None
    cache_restore_thread_id: str | None = None
    rerun_from_node_id: str | None = None
    rerun_subagent_action_node_id: str | None = None
    rerun_subagent_dag_id: str | None = None
    rerun_subagent_from_node_id: str | None = None
    current_action_id: str | None = None
    current_node_id: str | None = None
    current_action_objective: str | None = None
    current_action_item: object | None = None


def build_runtime_context(
    *,
    run_id: str,
    paths: RunPaths,
    state: RunStateSnapshot,
    run_timeout_seconds: int,
    max_llm_calls_per_turn: int = 1,
    max_subagent_calls_per_turn: int = 32,
    max_child_threads_per_turn: int = 8,
    thread_id: str | None = None,
    turn_id: str | None = None,
    id_factory: Callable[[], str] | None = None,
) -> RuntimeContext:
    thread = RuntimeThreadState(
        thread_id=thread_id or generate_ulid(),
        run_id=run_id,
        request_id=state.request_id,
        intent=state.intent,
        status="ready",
        total_turns=1,
    )
    turn = RuntimeTurnState(
        turn_id=turn_id or generate_ulid(),
        thread_id=thread.thread_id,
        run_id=run_id,
        request_id=state.request_id,
        turn_index=1,
        status="ready",
        intent=state.intent,
        objective=f"{state.intent}:{state.request_id}",
    )
    thread.active_turn_id = turn.turn_id
    state.thread_id = thread.thread_id
    state.active_turn_id = turn.turn_id
    write_json(paths.thread_state, thread.model_dump(mode="json"))
    write_json(paths.turn_state, turn.model_dump(mode="json"))
    write_json(paths.run_state, state.model_dump(mode="json"))
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
        thread=thread,
        turn=turn,
        agent_control=AgentControl(paths=paths, run_id=run_id, id_factory=id_factory),
        execution_policy=RuntimeExecutionPolicy(
            max_llm_calls_per_turn=max_llm_calls_per_turn,
            max_subagent_calls_per_turn=max_subagent_calls_per_turn,
            max_child_threads_per_turn=max_child_threads_per_turn,
        ),
        subagent_trace=[],
        dynamic_trace=[],
    )
    context.blackboard.bind_thread(thread.thread_id)
    context.control_plane.initialize(run_id=run_id, run_timeout_seconds=run_timeout_seconds)
    return context
