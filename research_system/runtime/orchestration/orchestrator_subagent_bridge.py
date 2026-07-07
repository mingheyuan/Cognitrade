from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.runtime import RuntimeItemRecord
from research_system.schemas.subagents import SubagentExecutionRecord, SubagentInvocation


class OrchestratorSubagentBridge:
    def __init__(self, host: Any) -> None:
        self.host = host
        self._attached_registry_ids: set[int] = set()
        self._attached_agent_control_ids: set[int] = set()

    def attach_observers(self) -> None:
        agent_control = getattr(getattr(self.host, "_active_runtime_context", None), "agent_control", None)
        if agent_control is not None and hasattr(agent_control, "add_observer") and id(agent_control) not in self._attached_agent_control_ids:
            agent_control.add_observer(self.handle_agent_control_event)
            self._attached_agent_control_ids.add(id(agent_control))
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
            registry_id = id(registry)
            if registry_id in self._attached_registry_ids:
                continue
            registry.add_observer(self.handle_registry_event)
            self._attached_registry_ids.add(registry_id)

    def handle_agent_control_event(self, *, stage: str, item: RuntimeItemRecord, payload: dict) -> None:
        runtime_context = getattr(self.host, "_active_runtime_context", None)
        if runtime_context is None or item.item_type not in {"agent", "subagent", "thread", "degradation"}:
            return
        context: RuntimeContext = runtime_context
        event_type = self._item_event_type(item_type=item.item_type, stage=stage)
        self.host._emit_event(
            context,
            request_id=context.state.request_id,
            intent=context.state.intent,
            event_type=event_type,
            phase=item.phase,
            agent=item.agent,
            message=f"{item.title} {stage}",
            payload={
                "item_id": item.item_id,
                "thread_id": item.thread_id,
                "turn_id": item.turn_id,
                "subagent_type": item.title,
                "status": item.status,
                "objective": item.objective,
                "degrade_reason": item.degrade_reason,
                "payload": item.payload,
            },
        )

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
        if invocation_context.get("child_thread_id"):
            payload["child_thread_id"] = invocation_context.get("child_thread_id")
        if invocation_context.get("child_turn_id"):
            payload["child_turn_id"] = invocation_context.get("child_turn_id")
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

    def _item_event_type(self, *, item_type: str, stage: str) -> str:
        if item_type == "degradation":
            return {
                "started": "runtime_degraded",
                "completed": "runtime_degraded",
                "failed": "runtime_degraded",
                "updated": "runtime_degraded",
            }.get(stage, "runtime_degraded")
        if item_type == "thread":
            return {
                "started": "thread_started",
                "completed": "thread_completed",
                "failed": "thread_failed",
                "updated": "thread_updated",
            }.get(stage, "thread_trace")
        if item_type == "agent":
            return {
                "started": "agent_started",
                "completed": "agent_completed",
                "failed": "agent_failed",
                "updated": "agent_updated",
            }.get(stage, "agent_trace")
        return {
            "started": "subagent_started",
            "completed": "subagent_completed",
            "failed": "subagent_failed",
            "updated": "subagent_updated",
        }.get(stage, "subagent_trace")
