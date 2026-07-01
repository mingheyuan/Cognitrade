from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from research_system.schemas.runtime import RunEvent


DisplayKind = Literal["thinking", "status", "event"]


@dataclass(frozen=True)
class DisplayChunk:
    kind: DisplayKind
    text: str
    state_patch: dict[str, str] | None = None


@dataclass
class PresenterState:
    phase_path: list[str] = field(default_factory=list)
    gates: dict[str, str] = field(default_factory=dict)
    active_subagent: str | None = None


class ChatStreamPresenter:
    def __init__(self, *, thinking_enabled: bool = True, events_mode: str = "compact") -> None:
        self.thinking_enabled = thinking_enabled
        self.events_mode = events_mode
        self.state = PresenterState()

    def on_event(self, event: RunEvent) -> list[DisplayChunk]:
        chunks: list[DisplayChunk] = []

        if self.events_mode == "full":
            chunks.append(DisplayChunk(kind="event", text=self._raw_event_text(event)))

        if event.event_type == "intent_identified":
            return chunks

        if event.event_type == "phase_started" and event.phase:
            if event.phase not in self.state.phase_path and event.phase != "gate":
                self.state.phase_path.append(event.phase)
                chunks.append(
                    DisplayChunk(
                        kind="status",
                        text=" > ".join(self.state.phase_path),
                        state_patch={"current_phase": event.phase},
                    )
                )
            return chunks

        if event.event_type == "subagent_started":
            summary = self._subagent_progress_summary(event, status="running")
            if summary:
                self.state.active_subagent = str((event.payload or {}).get("subagent_type") or "")
                chunks.append(
                    DisplayChunk(
                        kind="status",
                        text=summary,
                        state_patch={"current_phase": event.phase or (self.state.phase_path[-1] if self.state.phase_path else "subagent")},
                    )
                )
            return chunks

        if event.event_type == "subagent_completed":
            summary = self._subagent_progress_summary(event, status="completed")
            if summary:
                self.state.active_subagent = None
                chunks.append(DisplayChunk(kind="event", text=summary))
            return chunks

        if event.event_type == "subagent_failed":
            summary = self._subagent_progress_summary(event, status="failed")
            if summary:
                self.state.active_subagent = None
                chunks.append(DisplayChunk(kind="event", text=summary))
            return chunks

        if event.event_type == "agent_progress":
            if event.message.strip():
                chunks.append(DisplayChunk(kind="event", text=event.message.strip()))
            return chunks

        if event.event_type == "subagent_trace":
            if event.message.strip():
                chunks.append(DisplayChunk(kind="event", text=event.message.strip()))
            return chunks

        if event.event_type == "dynamic_action_inserted":
            if event.message.strip():
                chunks.append(DisplayChunk(kind="event", text=event.message.strip()))
            return chunks

        if event.event_type == "supervisor_decision":
            summary = self._supervisor_summary(event.payload or {})
            if summary:
                chunks.append(DisplayChunk(kind="event", text=summary))
            return chunks

        if event.event_type == "review_requested":
            return chunks

        if event.event_type == "review_completed":
            return chunks

        if event.event_type == "phase_completed" and event.phase == "gate":
            gate_type, verdict = self._parse_gate_message(event.message)
            if gate_type and verdict:
                self.state.gates[gate_type] = verdict
                if self.events_mode != "off" or verdict != "pass":
                    chunks.append(
                        DisplayChunk(
                            kind="status",
                            text=self._gate_summary(),
                            state_patch={"current_gate": self._gate_summary()},
                        )
                    )
            return chunks

        return chunks

    def _raw_event_text(self, event: RunEvent) -> str:
        tags: list[str] = [event.event_type]
        if event.phase:
            tags.append(event.phase)
        if event.agent:
            tags.append(event.agent)
        return f"[{'/'.join(tags)}] {event.message}"

    def _parse_gate_message(self, message: str) -> tuple[str | None, str | None]:
        if ":" not in message:
            return None, None
        gate_type, verdict = message.split(":", 1)
        return gate_type.strip(), verdict.strip()

    def _gate_summary(self) -> str:
        if not self.state.gates:
            return "gate: pending"
        ordered = [f"{gate} {verdict}" for gate, verdict in sorted(self.state.gates.items())]
        return "gate: " + " | ".join(ordered)

    def _supervisor_summary(self, payload: dict) -> str | None:
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

    def _subagent_progress_summary(self, event: RunEvent, *, status: str) -> str | None:
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
