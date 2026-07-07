from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.planning import ActionSpec
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry, RunEvent


class OrchestratorExecutionLifecycleSupport:
    def __init__(self, host: Any) -> None:
        self.host = host

    def begin_action(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        intent: str,
        action: ActionSpec,
        control_support,
    ) -> None:
        phase = action.phase
        agent = action.agent
        context.current_action_id = action.action_id
        context.current_node_id = action.node_id
        context.current_action_objective = action.objective
        action_item = context.agent_control.spawn(
            thread_id=context.thread.thread_id,
            turn_id=context.turn.turn_id,
            request_id=request.request_id,
            agent=agent,
            phase=phase,
            title=f"{phase}:{agent}",
            objective=action.objective,
            payload={
                "action_id": action.action_id,
                "node_id": action.node_id,
            },
            item_type="action",
        )
        context.state.active_item_id = action_item.item_id
        context.turn.current_item_id = action_item.item_id
        context.current_action_item = action_item
        context.turn.status = "running"
        context.thread.status = "running"
        context.agent_control.send(
            action_item,
            payload={
                "node_id": action.node_id,
                "phase": phase,
                "agent": agent,
            },
        )
        self.update_state(context, current_phase=phase, active_agent=agent)
        self.emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="phase_started",
            phase=phase,
            agent=agent,
            message=f"Phase started: {phase}",
            payload={
                "action_id": action.action_id,
                "node_id": action.node_id,
                "objective": action.objective,
            },
        )
        control_support.enforce_runtime_controls(context, request_id=request.request_id, intent=intent)
        self.emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="agent_started",
            phase=phase,
            agent=agent,
            message=f"Agent started: {agent}",
            payload={
                "action_id": action.action_id,
                "node_id": action.node_id,
                "objective": action.objective,
            },
        )

    def complete_action(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        intent: str,
        phase: str,
        agent: str,
        completed_node_ids: list[str],
        pending_actions_after_current: list[ActionSpec],
        control_support,
    ) -> None:
        control_support.enforce_runtime_controls(context, request_id=request.request_id, intent=intent)
        self.emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="agent_completed",
            phase=phase,
            agent=agent,
            message=f"Agent completed: {agent}",
        )
        self.emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="phase_completed",
            phase=phase,
            agent=agent,
            message=f"Phase completed: {phase}",
        )
        self.update_state(
            context,
            pending_nodes=[item.node_id for item in pending_actions_after_current],
            completed_nodes=completed_node_ids + [context.current_node_id],
        )
        self._complete_action_item(context)
        context.current_action_id = None
        context.current_node_id = None
        context.current_action_objective = None
        context.checkpoint_store.save(context.state)

    def add_entry(self, context: RuntimeContext, request: RequestEnvelope, intent: str, entry: BlackboardEntry) -> None:
        context.blackboard.add(entry)
        self.emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="blackboard_entry_added",
            phase=entry.phase,
            agent=entry.agent,
            message=f"Blackboard entry added: {entry.kind}",
            payload={"entry_id": entry.entry_id, "kind": entry.kind},
        )

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

    def _complete_action_item(self, context: RuntimeContext) -> None:
        action_item = getattr(context, "current_action_item", None)
        if action_item is None:
            return
        context.agent_control.wait(
            action_item,
            status="completed",
            payload={"completed_item_id": action_item.item_id},
        )
        context.state.active_item_id = None
        context.turn.current_item_id = None
        context.turn.status = "completed"
        context.thread.status = "running"
        context.current_action_item = None
