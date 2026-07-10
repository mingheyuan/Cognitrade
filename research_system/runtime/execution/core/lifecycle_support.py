from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.runtime import RunEvent


class OrchestratorExecutionLifecycleSupport:
    def __init__(self, host: Any) -> None:
        self.host = host

    def emit_event(
        self,
        context: RuntimeContext,
        *,
        request_id: str,
        intent: str,
        event_type: str,
        message: str,
        phase: str | None = None,
        agent: str | None = None,
        payload: dict | None = None,
    ) -> None:
        event = RunEvent(
            request_id=request_id,
            run_id=context.run_id,
            intent=intent,
            event_type=event_type,
            phase=phase,
            agent=agent,
            message=message,
            payload=payload,
        )
        context.event_stream.emit(event)
        if self.host.event_listener is not None:
            self.host.event_listener(event)
        self.update_state(context, last_event_id=event.event_id)

    def transition(
        self,
        context: RuntimeContext,
        status: str,
        *,
        request_id: str,
        intent: str,
        message: str,
        error_summary: str | None = None,
    ) -> None:
        context.state.status = status  # type: ignore[assignment]
        if error_summary is not None:
            context.state.error_summary = error_summary
        context.state.updated_at = context.state.updated_at.__class__.now(context.state.updated_at.tzinfo)
        write_json(context.paths.run_state, context.state.model_dump(mode="json"))
        context.checkpoint_store.save(context.state)
        context.logger.log(context.run_id, "INFO", "orchestrator", message, {"status": status})

    def update_state(
        self,
        context: RuntimeContext,
        *,
        current_phase: str | None = None,
        active_agent: str | None = None,
        pending_nodes: list[str] | None = None,
        completed_nodes: list[str] | None = None,
        last_event_id: str | None = None,
    ) -> None:
        if current_phase is not None:
            context.state.current_phase = current_phase
        if active_agent is not None:
            context.state.active_agent = active_agent
        if pending_nodes is not None:
            context.state.pending_nodes = pending_nodes
        if completed_nodes is not None:
            context.state.completed_nodes = completed_nodes
        if last_event_id is not None:
            context.state.last_event_id = last_event_id
        context.state.thread_id = context.thread.thread_id
        context.state.active_turn_id = context.turn.turn_id
        write_json(context.paths.run_state, context.state.model_dump(mode="json"))
        write_json(context.paths.thread_state, context.thread.model_dump(mode="json"))
        write_json(context.paths.turn_state, context.turn.model_dump(mode="json"))
