from __future__ import annotations

from pathlib import Path

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope
from research_system.schemas.runtime import BlackboardEntry


class OrchestratorCompatSupport:
    def __init__(self, host) -> None:
        self.host = host

    def execute_plan(self, *, context: RuntimeContext, request: RequestEnvelope, plan: ActionPlan) -> None:
        self.host._plan_executor().execute_plan(
            context=context,
            request=request,
            plan=plan,
        )

    def run_action(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        intent: str,
        action: ActionSpec,
        completed_node_ids: list[str],
        pending_actions_after_current: list[ActionSpec],
    ) -> None:
        self.host._action_runner().run_action(
            context=context,
            request=request,
            plan=plan,
            intent=intent,
            action=action,
            completed_node_ids=completed_node_ids,
            pending_actions_after_current=pending_actions_after_current,
        )

    def add_entry(self, context: RuntimeContext, request: RequestEnvelope, intent: str, entry: BlackboardEntry) -> None:
        self.host._execution_support().add_entry(context, request, intent, entry)

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

    def extend_subagent_trace(self, context: RuntimeContext, *, agent: str, phase: str, payload: dict | None) -> None:
        self.host._execution_support().extend_subagent_trace(context, agent=agent, phase=phase, payload=payload)

    def handle_subagent_registry_event(
        self,
        *,
        stage: str,
        invocation,
        record,
        outputs: dict | None,
        error: Exception | None,
    ) -> None:
        self.host._subagent_bridge().handle_registry_event(
            stage=stage,
            invocation=invocation,
            record=record,
            outputs=outputs,
            error=error,
        )

    def trace_task_name(self, payload: dict) -> str:
        return self.host._execution_support().trace_task_name(payload)

    def trace_agent_path(self, payload: dict) -> str:
        return self.host._execution_support().trace_agent_path(payload)

    def trace_agent_label(self, payload: dict) -> str:
        return self.host._execution_support().trace_agent_label(payload)

    def enforce_runtime_controls(self, context: RuntimeContext, *, request_id: str, intent: str) -> None:
        self.host._execution_support().enforce_runtime_controls(context, request_id=request_id, intent=intent)

    def wait_for_external_dependency(self, context: RuntimeContext, *, request_id: str, intent: str) -> None:
        self.host._execution_support().wait_for_external_dependency(context, request_id=request_id, intent=intent)
