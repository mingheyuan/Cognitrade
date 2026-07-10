from __future__ import annotations

from pathlib import Path

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope


class OrchestratorCompatSupport:
    def __init__(self, host) -> None:
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
        self.host._execution_support().emit_event(
            context,
            request_id=request_id,
            intent=intent,
            event_type=event_type,
            message=message,
            phase=phase,
            agent=agent,
            payload=payload,
        )

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
        self.host._execution_support().transition(
            context,
            status,
            request_id=request_id,
            intent=intent,
            message=message,
            error_summary=error_summary,
        )

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
        self.host._execution_support().update_state(
            context,
            current_phase=current_phase,
            active_agent=active_agent,
            pending_nodes=pending_nodes,
            completed_nodes=completed_nodes,
            last_event_id=last_event_id,
        )

    def build_response(
        self,
        *,
        request: RequestEnvelope,
        run_id: str,
        intent: str,
        status: str,
        message: str,
        result: dict | None,
        phase: str | None,
        active_agent: str | None,
        latest_event: str | None,
        percent: int,
        errors: list[str] | None = None,
    ) -> ResponseEnvelope:
        return self.host._runtime_io().build_response(
            request=request,
            run_id=run_id,
            intent=intent,
            status=status,
            message=message,
            result=result,
            phase=phase,
            active_agent=active_agent,
            latest_event=latest_event,
            percent=percent,
            errors=errors,
        )

    def load_result_payload(self, result_path: Path) -> dict | None:
        return self.host._runtime_io().load_result_payload(result_path)

    def load_json_payload(self, path: Path) -> dict | None:
        return self.host._runtime_io().load_json_payload(path)

    def trace_task_name(self, payload: dict) -> str:
        return self.host._execution_support().trace_task_name(payload)

    def trace_agent_path(self, payload: dict) -> str:
        return self.host._execution_support().trace_agent_path(payload)

    def trace_agent_label(self, payload: dict) -> str:
        return self.host._execution_support().trace_agent_label(payload)

    def enforce_run_controls(self, context: RuntimeContext, *, request_id: str, intent: str) -> None:
        self.host._execution_support().enforce_run_controls(context, request_id=request_id, intent=intent)

    def wait_for_external_dependency(self, context: RuntimeContext, *, request_id: str, intent: str) -> None:
        self.host._execution_support().wait_for_external_dependency(context, request_id=request_id, intent=intent)
