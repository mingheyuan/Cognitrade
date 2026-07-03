from __future__ import annotations

from typing import Any


class RunTraceRowBuilder:
    def event_timeline_row(
        self,
        *,
        event_type: str,
        phase: str | None,
        agent: str | None,
        message: str,
        payload: dict[str, Any] | None,
        created_at,
    ) -> dict[str, Any]:
        row_payload = dict(payload or {})
        return {
            "event_type": event_type,
            "phase": phase,
            "agent": agent,
            "message": message,
            "action_id": row_payload.get("action_id"),
            "node_id": row_payload.get("node_id"),
            "task_name": row_payload.get("task_name"),
            "agent_path": row_payload.get("agent_path"),
            "agent_label": row_payload.get("agent_label"),
            "subagent_type": row_payload.get("subagent_type"),
            "invocation_id": row_payload.get("invocation_id"),
            "status": row_payload.get("status"),
            "objective": row_payload.get("objective"),
            "notes": list(row_payload.get("notes") or []),
            "wait_timeout_seconds": row_payload.get("wait_timeout_seconds"),
            "created_at": created_at,
        }

    def subagent_trace_row(
        self,
        *,
        event_type: str,
        phase: str | None,
        agent: str | None,
        payload: dict[str, Any] | None,
        created_at,
        derive_labels: bool,
        fallback_status: bool,
    ) -> dict[str, Any]:
        row_payload = dict(payload or {})
        node_id = str(row_payload.get("node_id") or "").strip()
        subagent_type = str(row_payload.get("subagent_type") or "").strip()
        resolved_agent = str(agent or row_payload.get("agent") or "").strip()
        task_name = row_payload.get("task_name")
        if derive_labels and not task_name:
            task_name = f"{node_id}:{subagent_type}" if node_id and subagent_type else (node_id or subagent_type or None)
        agent_path = row_payload.get("agent_path")
        if derive_labels and not agent_path:
            agent_path = f"{resolved_agent}/{subagent_type}" if resolved_agent and subagent_type else (resolved_agent or subagent_type or None)
        agent_label = row_payload.get("agent_label")
        if derive_labels and not agent_label and subagent_type:
            agent_label = " ".join(segment.capitalize() for segment in subagent_type.split("_") if segment).strip() or subagent_type
        status = row_payload.get("status")
        if fallback_status and not status:
            status = self.subagent_status(event_type)
        return {
            "event_type": event_type,
            "phase": phase,
            "agent": agent,
            "subagent_type": row_payload.get("subagent_type"),
            "invocation_id": row_payload.get("invocation_id"),
            "action_id": row_payload.get("action_id"),
            "node_id": row_payload.get("node_id"),
            "action_objective": row_payload.get("action_objective"),
            "task_name": task_name,
            "agent_path": agent_path,
            "agent_label": agent_label,
            "status": status,
            "objective": row_payload.get("objective"),
            "notes": list(row_payload.get("notes") or []),
            "created_at": created_at,
        }

    def dynamic_trace_row(
        self,
        *,
        phase: str | None,
        agent: str | None,
        payload: dict[str, Any] | None,
        created_at,
        default_context: bool,
    ) -> dict[str, Any]:
        row_payload = dict(payload or {})
        row_payload.setdefault("event", "dynamic_action_inserted")
        if default_context:
            row_payload.setdefault("phase", phase)
            row_payload.setdefault("agent", agent)
            row_payload.setdefault("action_id", row_payload.get("action_id"))
            row_payload.setdefault("node_id", row_payload.get("node_id"))
            row_payload.setdefault("objective", row_payload.get("objective"))
        row_payload.setdefault("created_at", created_at)
        return row_payload

    def wait_trace_row(
        self,
        *,
        event_type: str,
        phase: str | None,
        agent: str | None,
        message: str,
        payload: dict[str, Any] | None,
        created_at,
    ) -> dict[str, Any] | None:
        row_payload = dict(payload or {})
        if event_type == "phase_started" and message == "Entered waiting_external state":
            return {
                "event": "waiting_external_started",
                "action_id": row_payload.get("action_id"),
                "node_id": row_payload.get("node_id"),
                "phase": phase,
                "agent": agent,
                "objective": row_payload.get("objective"),
                "wait_timeout_seconds": row_payload.get("wait_timeout_seconds"),
                "created_at": created_at,
            }
        if event_type == "run_timed_out" and "External wait timed out" in message:
            return {
                "event": "waiting_external_timed_out",
                "created_at": created_at,
            }
        if event_type == "run_cancelled" and "waiting" in message.lower():
            return {
                "event": "waiting_external_cancelled",
                "created_at": created_at,
            }
        return None

    @staticmethod
    def subagent_status(event_type: str) -> str | None:
        return {
            "subagent_started": "running",
            "subagent_completed": "completed",
            "subagent_failed": "failed",
        }.get(event_type)
