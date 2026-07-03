from __future__ import annotations

from research_system.runtime.chat_presenter_live_accumulator import ChatPresenterLiveAccumulator
from research_system.runtime.chat_presenter_payload_state import (
    ChatPresenterPayloadState,
    PresenterState,
)
from research_system.runtime.run_trace_row_builder import RunTraceRowBuilder
from research_system.schemas.runtime import RunEvent


class ChatPresenterLivePayloads:
    def __init__(self, state: PresenterState, row_builder: RunTraceRowBuilder | None = None) -> None:
        self.state = state
        self.row_builder = row_builder or RunTraceRowBuilder()
        self.payload_state = ChatPresenterPayloadState(state)
        self.accumulator = ChatPresenterLiveAccumulator(self.payload_state, self.row_builder)

    def current_trace_payload(self):
        return self.payload_state.current_trace_payload()

    def current_events_payload(self):
        return self.payload_state.current_events_payload()

    def update_live_payloads(self, event: RunEvent) -> None:
        self.accumulator.update(event)
