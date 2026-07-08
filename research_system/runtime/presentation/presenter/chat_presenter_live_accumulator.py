from __future__ import annotations

from typing import Any

from research_system.runtime.presentation.presenter.chat_presenter_payload_state import ChatPresenterPayloadState
from research_system.runtime.history.session.trace.run_trace_row_builder import RunTraceRowBuilder
from research_system.schemas.runtime import RunEvent


class ChatPresenterLiveAccumulator:
    def __init__(
        self,
        payload_state: ChatPresenterPayloadState,
        row_builder: RunTraceRowBuilder | None = None,
    ) -> None:
        self.payload_state = payload_state
        self.row_builder = row_builder or RunTraceRowBuilder()

    def update(self, event: RunEvent) -> None:
        trace_payload = self.payload_state.ensure_trace_payload(event)
        event_counts = trace_payload.setdefault("event_counts", {})
        event_counts["events"] = int(event_counts.get("events") or 0) + 1

        if event.event_type in {
            "subagent_started",
            "subagent_completed",
            "subagent_failed",
            "subagent_trace",
            "thread_started",
            "thread_completed",
            "thread_failed",
            "thread_trace",
        }:
            trace_payload.setdefault("subagent_trace", []).append(self._subagent_trace_row(event))
        elif event.event_type == "action_plan_patch_applied":
            trace_payload.setdefault("action_plan_patches", []).append(self._action_plan_patch_row(event))
        elif (
            event.event_type == "dynamic_action_inserted"
            or event.event_type.startswith("subagent_dag_")
            or event.event_type.startswith("subagent_phase_shadow_")
            or event.event_type.startswith("action_phase_shadow_")
        ):
            trace_payload.setdefault("dynamic_trace", []).append(self._dynamic_trace_row(event))
        else:
            wait_row = self._wait_trace_row(event)
            if wait_row is not None:
                trace_payload.setdefault("wait_trace", []).append(wait_row)

        event_counts["subagent_trace"] = len(trace_payload.get("subagent_trace") or [])
        event_counts["dynamic_trace"] = len(trace_payload.get("dynamic_trace") or [])
        event_counts["action_plan_patches"] = len(trace_payload.get("action_plan_patches") or [])
        event_counts["wait_trace"] = len(trace_payload.get("wait_trace") or [])
        self._update_events_payload(event)

    def _update_events_payload(self, event: RunEvent) -> None:
        payload = self.payload_state.ensure_events_payload(event)
        payload.setdefault("events", []).append(self._event_timeline_row(event))
        event_counts = payload.setdefault("event_counts", {})
        event_counts["events"] = len(payload.get("events") or [])
        event_counts["distinct_event_types"] = len(
            {str(row.get("event_type") or "") for row in payload.get("events") or [] if isinstance(row, dict)}
        )

    def _event_timeline_row(self, event: RunEvent) -> dict[str, Any]:
        return self.row_builder.event_timeline_row(
            event_type=event.event_type,
            phase=event.phase,
            agent=event.agent,
            message=event.message,
            payload=event.payload or {},
            created_at=event.created_at.isoformat(),
        )

    def _subagent_trace_row(self, event: RunEvent) -> dict[str, Any]:
        return self.row_builder.subagent_trace_row(
            event_type=event.event_type,
            phase=event.phase,
            agent=event.agent,
            payload=event.payload or {},
            created_at=event.created_at.isoformat(),
            derive_labels=True,
            fallback_status=True,
        )

    def _dynamic_trace_row(self, event: RunEvent) -> dict[str, Any]:
        return self.row_builder.dynamic_trace_row(
            event_type=event.event_type,
            phase=event.phase,
            agent=event.agent,
            payload=event.payload or {},
            created_at=event.created_at.isoformat(),
            default_context=True,
        )

    def _action_plan_patch_row(self, event: RunEvent) -> dict[str, Any]:
        return self.row_builder.action_plan_patch_row(
            event_type=event.event_type,
            phase=event.phase,
            agent=event.agent,
            payload=event.payload or {},
            created_at=event.created_at.isoformat(),
            default_context=True,
        )

    def _wait_trace_row(self, event: RunEvent) -> dict[str, Any] | None:
        return self.row_builder.wait_trace_row(
            event_type=event.event_type,
            phase=event.phase,
            agent=event.agent,
            message=str(event.message or ""),
            payload=event.payload or {},
            created_at=event.created_at.isoformat(),
        )


__all__ = ["ChatPresenterLiveAccumulator"]
