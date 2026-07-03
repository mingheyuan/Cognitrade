from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.subagents import SubagentExecutionRecord, SubagentInvocation


class OrchestratorSubagentBridge:
    def __init__(self, host: Any) -> None:
        self.host = host

    def attach_observers(self) -> None:
        for service in (
            self.host.collector,
            self.host.distiller,
            self.host.critic,
            self.host.retriever,
            self.host.reporter,
        ):
            registry = getattr(service, "registry", None)
            if registry is None or not hasattr(registry, "add_observer"):
                continue
            registry.add_observer(self.handle_registry_event)

    def handle_registry_event(
        self,
        *,
        stage: str,
        invocation: SubagentInvocation,
        record: SubagentExecutionRecord | None,
        outputs: dict | None,
        error: Exception | None,
    ) -> None:
        runtime_context = getattr(self.host, "_active_runtime_context", None)
        if runtime_context is None:
            return
        context: RuntimeContext = runtime_context
        phase = context.state.current_phase
        agent = context.state.active_agent
        payload = {
            "subagent_type": invocation.subagent_type,
            "invocation_id": invocation.invocation_id,
            "objective": invocation.objective,
            "status": record.status if record is not None else ("failed" if stage == "failed" else "running"),
            "notes": list((record.notes if record is not None else []) or []),
            "output_keys": list((record.output_keys if record is not None else []) or []),
            "action_id": context.current_action_id,
            "node_id": context.current_node_id,
            "action_objective": context.current_action_objective,
        }
        invocation_context = dict(invocation.context or {})
        if invocation_context.get("task_name"):
            payload["task_name"] = invocation_context.get("task_name")
        if invocation_context.get("agent_path"):
            payload["agent_path"] = invocation_context.get("agent_path")
        payload["task_name"] = payload.get("task_name") or self.host._execution_support().trace_task_name(payload)
        payload["agent_path"] = payload.get("agent_path") or self.host._execution_support().trace_agent_path(payload)
        payload["agent_label"] = payload.get("agent_label") or self.host._execution_support().trace_agent_label(payload)
        if error is not None and not payload["notes"]:
            payload["notes"] = [f"{error.__class__.__name__}: {error}"]
        event_type = {
            "started": "subagent_started",
            "completed": "subagent_completed",
            "failed": "subagent_failed",
        }.get(stage, "subagent_trace")
        message = f"{invocation.subagent_type} {stage}"
        self.host._emit_event(
            context,
            request_id=context.state.request_id,
            intent=context.state.intent,
            event_type=event_type,
            phase=phase,
            agent=agent,
            message=message,
            payload=payload,
        )
