from __future__ import annotations

from typing import Any

from research_system.runtime.orchestrator_context import RuntimeContext
from research_system.runtime.orchestrator_execution_support import OrchestratorExecutionSupport
from research_system.schemas.planning import ActionSpec
from research_system.schemas.protocol import RequestEnvelope


def handle_collector_wait(
    host: Any,
    support: OrchestratorExecutionSupport,
    *,
    context: RuntimeContext,
    request: RequestEnvelope,
    intent: str,
    action: ActionSpec,
    phase: str,
    payload: dict,
) -> None:
    if not payload.get("requires_wait"):
        return
    support.transition(
        context,
        "waiting_external",
        request_id=request.request_id,
        intent=intent,
        message="Waiting for external dependency",
    )
    context.control_plane.set_wait_deadline(wait_timeout_seconds=host.settings.external_wait_timeout_seconds)
    support.emit_event(
        context,
        request_id=request.request_id,
        intent=intent,
        event_type="phase_started",
        phase=phase,
        agent="collector",
        message="Entered waiting_external state",
        payload={
            "wait_timeout_seconds": host.settings.external_wait_timeout_seconds,
            "action_id": action.action_id,
            "node_id": action.node_id,
            "objective": action.objective,
        },
    )
    support.wait_for_external_dependency(context, request_id=request.request_id, intent=intent)
    context.control_plane.clear_wait_deadline()
    support.transition(
        context,
        "running",
        request_id=request.request_id,
        intent=intent,
        message="External dependency resolved",
    )
