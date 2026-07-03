from __future__ import annotations

from typing import Any

from research_system.runtime.history.session.trace.debug_support import SessionHistoryDebugSupport
from research_system.runtime.history.session.trace.run_trace_row_builder import RunTraceRowBuilder
from research_system.runtime.history.session.trace.subagent_payload_builder import (
    SessionHistorySubagentPayloadBuilder,
)
from research_system.runtime.history.session.trace.trace_sections import SessionHistoryTraceSections


class SessionHistoryTraceRebuilder:
    def __init__(
        self,
        row_builder: RunTraceRowBuilder | None = None,
        subagent_payload_builder: SessionHistorySubagentPayloadBuilder | None = None,
        debug_support: SessionHistoryDebugSupport | None = None,
        trace_sections: SessionHistoryTraceSections | None = None,
    ) -> None:
        self.row_builder = row_builder or RunTraceRowBuilder()
        self.subagent_payload_builder = subagent_payload_builder or SessionHistorySubagentPayloadBuilder()
        self.debug_support = debug_support or SessionHistoryDebugSupport()
        self.trace_sections = trace_sections or SessionHistoryTraceSections(row_builder=self.row_builder)

    def debug_payload(self, result_payload: dict[str, Any]) -> dict[str, Any]:
        return self.debug_support.debug_payload(result_payload)

    def subagent_trace(self, debug_payload: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.trace_sections.subagent_trace(debug_payload, events)

    def dynamic_trace(self, debug_payload: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.trace_sections.dynamic_trace(debug_payload, events)

    def wait_trace(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.trace_sections.wait_trace(events)

    def event_timeline(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.trace_sections.event_timeline(events)

    def build_subagent_payload(
        self,
        *,
        run_id: str,
        trace_payload: dict[str, Any],
        events_payload: dict[str, Any],
        invocation_id: str,
    ) -> dict[str, Any]:
        return self.subagent_payload_builder.build_payload(
            run_id=run_id,
            trace_payload=trace_payload,
            events_payload=events_payload,
            invocation_id=invocation_id,
        )


__all__ = ["SessionHistoryTraceRebuilder"]
