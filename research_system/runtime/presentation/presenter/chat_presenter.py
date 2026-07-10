from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from research_system.runtime.presentation.presenter.chat_presenter_live_payloads import (
    ChatPresenterLivePayloads,
)
from research_system.runtime.presentation.presenter.chat_presenter_payload_state import PresenterState
from research_system.runtime.presentation.presenter.chat_presenter_summaries import ChatPresenterSummaries
from research_system.schemas.runtime import RunEvent


DisplayKind = Literal["thinking", "status", "event"]


@dataclass(frozen=True)
class DisplayChunk:
    kind: DisplayKind
    text: str
    state_patch: dict[str, str] | None = None


class ChatStreamPresenter:
    def __init__(self, *, thinking_enabled: bool = True, events_mode: str = "compact") -> None:
        self.thinking_enabled = thinking_enabled
        self.events_mode = events_mode
        self.state = PresenterState()
        self.live_payloads = ChatPresenterLivePayloads(self.state)
        self.summaries = ChatPresenterSummaries(self.state)

    def on_event(self, event: RunEvent) -> list[DisplayChunk]:
        self.live_payloads.update_live_payloads(event)
        chunks: list[DisplayChunk] = []

        if self.events_mode == "full":
            chunks.append(DisplayChunk(kind="event", text=self.summaries.raw_event_text(event)))

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

        if event.event_type in {"subagent_started", "thread_started"}:
            summary = self.summaries.subagent_progress_summary(event, status="running")
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

        if event.event_type in {"subagent_completed", "thread_completed"}:
            summary = self.summaries.subagent_progress_summary(event, status="completed")
            if summary:
                self.state.active_subagent = None
                chunks.append(DisplayChunk(kind="event", text=summary))
            return chunks

        if event.event_type in {"subagent_failed", "thread_failed"}:
            summary = self.summaries.subagent_progress_summary(event, status="failed")
            if summary:
                self.state.active_subagent = None
                chunks.append(DisplayChunk(kind="event", text=summary))
            return chunks

        if event.event_type == "agent_progress":
            if event.message.strip():
                chunks.append(DisplayChunk(kind="event", text=event.message.strip()))
            return chunks

        if event.event_type in {"subagent_trace", "thread_trace"}:
            if event.message.strip():
                chunks.append(DisplayChunk(kind="event", text=event.message.strip()))
            return chunks

        if event.event_type == "review_requested":
            return chunks

        if event.event_type == "review_completed":
            return chunks

        if event.event_type == "phase_completed" and event.phase == "gate":
            gate_type, verdict = self.summaries.parse_gate_message(event.message)
            if gate_type and verdict:
                self.state.gates[gate_type] = verdict
                gate_summary = self.summaries.gate_summary()
                if self.events_mode != "off" or verdict != "pass":
                    chunks.append(
                        DisplayChunk(
                            kind="status",
                            text=gate_summary,
                            state_patch={"current_gate": gate_summary},
                        )
                    )
            return chunks

        return chunks

    def current_trace_payload(self):
        return self.live_payloads.current_trace_payload()

    def current_events_payload(self):
        return self.live_payloads.current_events_payload()


__all__ = ["ChatStreamPresenter", "DisplayChunk", "DisplayKind"]
