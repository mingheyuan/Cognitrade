from __future__ import annotations

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext


class OrchestratorExecutionTraceSupport:
    def __init__(self, *, lifecycle_support) -> None:
        self.lifecycle_support = lifecycle_support

    def extend_subagent_trace(self, context: RuntimeContext, *, agent: str, phase: str, payload: dict | None) -> None:
        trace_items = list((payload or {}).get("subagent_trace") or [])
        for item in trace_items:
            if not isinstance(item, dict):
                continue
            record = dict(item)
            record.setdefault("invocation_id", record.get("invocation_id"))
            record.setdefault("agent", agent)
            record.setdefault("phase", phase)
            record.setdefault("action_id", context.current_action_id)
            record.setdefault("node_id", context.current_node_id)
            record.setdefault("action_objective", context.current_action_objective)
            record.setdefault("task_name", self.trace_task_name(record))
            record.setdefault("agent_path", self.trace_agent_path(record))
            record.setdefault("agent_label", self.trace_agent_label(record))
            context.subagent_trace.append(record)
            subagent_type = str(record.get("subagent_type") or agent).strip()
            status = str(record.get("status") or "completed").strip()
            objective = str(record.get("objective") or "").strip()
            message = f"{subagent_type} [{status}]"
            if objective:
                message = f"{message}: {objective}"
            self.lifecycle_support.emit_event(
                context,
                request_id=context.state.request_id,
                intent=context.state.intent,
                event_type="subagent_trace",
                phase=phase,
                agent=agent,
                message=message,
                payload=record,
            )

    def trace_task_name(self, payload: dict) -> str:
        node_id = str(payload.get("node_id") or "").strip()
        subagent_type = str(payload.get("subagent_type") or "").strip()
        if node_id and subagent_type:
            return f"{node_id}:{subagent_type}"
        return node_id or subagent_type or "task"

    def trace_agent_path(self, payload: dict) -> str:
        agent = str(payload.get("agent") or "").strip()
        subagent_type = str(payload.get("subagent_type") or "").strip()
        if agent and subagent_type:
            return f"{agent}/{subagent_type}"
        return agent or subagent_type or "-"

    def trace_agent_label(self, payload: dict) -> str:
        explicit = str(payload.get("agent_label") or "").strip()
        if explicit:
            return explicit
        subagent_type = str(payload.get("subagent_type") or "").strip()
        if not subagent_type:
            return str(payload.get("task_name") or payload.get("node_id") or "child_agent").strip() or "child_agent"
        return " ".join(segment.capitalize() for segment in subagent_type.split("_") if segment).strip() or subagent_type
