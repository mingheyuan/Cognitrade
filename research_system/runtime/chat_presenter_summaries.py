from __future__ import annotations

from research_system.runtime.chat_presenter_payload_state import PresenterState
from research_system.schemas.runtime import RunEvent


class ChatPresenterSummaries:
    def __init__(self, state: PresenterState) -> None:
        self.state = state

    def raw_event_text(self, event: RunEvent) -> str:
        tags: list[str] = [event.event_type]
        if event.phase:
            tags.append(event.phase)
        if event.agent:
            tags.append(event.agent)
        return f"[{'/'.join(tags)}] {event.message}"

    def parse_gate_message(self, message: str) -> tuple[str | None, str | None]:
        if ":" not in message:
            return None, None
        gate_type, verdict = message.split(":", 1)
        return gate_type.strip(), verdict.strip()

    def gate_summary(self) -> str:
        if not self.state.gates:
            return "gate: pending"
        ordered = [f"{gate} {verdict}" for gate, verdict in sorted(self.state.gates.items())]
        return "gate: " + " | ".join(ordered)

    def supervisor_summary(self, payload: dict) -> str | None:
        decision_type = str(payload.get("decision_type") or "").strip()
        next_actions = payload.get("next_actions") or []
        notes = payload.get("notes") or []
        if decision_type == "stop":
            note = str(notes[0]).strip() if notes else "Supervisor stopped because no more actions are needed."
            return note
        if decision_type == "continue" and next_actions:
            first = next_actions[0]
            if isinstance(first, dict):
                node_id = str(first.get("node_id") or "").strip()
                phase = str(first.get("phase") or "").strip()
                agent = str(first.get("agent") or "").strip()
                parts = [part for part in (phase, agent, node_id) if part]
                if parts:
                    return "next: " + " / ".join(parts)
        return None

    def subagent_progress_summary(self, event: RunEvent, *, status: str) -> str | None:
        payload = event.payload or {}
        subagent_type = str(payload.get("subagent_type") or "").strip()
        if not subagent_type:
            return event.message.strip() or None
        objective = str(payload.get("objective") or "").strip()
        notes = [str(item).strip() for item in payload.get("notes") or [] if str(item).strip()]
        if status == "running":
            if objective:
                return f"{subagent_type}: {objective}"
            return f"{subagent_type}: running"
        if status == "failed":
            detail = notes[0] if notes else "failed"
            return f"{subagent_type}: failed ({detail})"
        if objective:
            return f"{subagent_type}: completed"
        return f"{subagent_type}: completed"
