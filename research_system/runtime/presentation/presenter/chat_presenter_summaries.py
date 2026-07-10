from __future__ import annotations

from research_system.runtime.presentation.presenter.chat_presenter_payload_state import PresenterState
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


__all__ = ["ChatPresenterSummaries"]
