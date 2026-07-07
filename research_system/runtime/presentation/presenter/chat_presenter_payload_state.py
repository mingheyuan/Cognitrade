from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_system.schemas.runtime import RunEvent


@dataclass
class PresenterState:
    phase_path: list[str] = field(default_factory=list)
    gates: dict[str, str] = field(default_factory=dict)
    active_subagent: str | None = None
    trace_payload: dict[str, Any] | None = None
    events_payload: dict[str, Any] | None = None


class ChatPresenterPayloadState:
    def __init__(self, state: PresenterState) -> None:
        self.state = state

    def current_trace_payload(self) -> dict[str, Any] | None:
        payload = self.state.trace_payload
        if not isinstance(payload, dict):
            return None
        return {
            "run_id": payload.get("run_id"),
            "request": dict(payload.get("request") or {}),
            "summary": dict(payload.get("summary") or {}),
            "display_summary": payload.get("display_summary"),
            "subagent_trace": [dict(item) for item in payload.get("subagent_trace") or [] if isinstance(item, dict)],
            "dynamic_trace": [dict(item) for item in payload.get("dynamic_trace") or [] if isinstance(item, dict)],
            "wait_trace": [dict(item) for item in payload.get("wait_trace") or [] if isinstance(item, dict)],
            "threads": [dict(item) for item in payload.get("threads") or [] if isinstance(item, dict)],
            "thread_tree": [dict(item) for item in payload.get("thread_tree") or [] if isinstance(item, dict)],
            "event_counts": dict(payload.get("event_counts") or {}),
        }

    def current_events_payload(self) -> dict[str, Any] | None:
        payload = self.state.events_payload
        if not isinstance(payload, dict):
            return None
        return {
            "run_id": payload.get("run_id"),
            "request": dict(payload.get("request") or {}),
            "summary": dict(payload.get("summary") or {}),
            "display_summary": payload.get("display_summary"),
            "events": [dict(item) for item in payload.get("events") or [] if isinstance(item, dict)],
            "event_counts": dict(payload.get("event_counts") or {}),
        }

    def ensure_trace_payload(self, event: RunEvent) -> dict[str, Any]:
        payload = self.state.trace_payload
        if not isinstance(payload, dict) or str(payload.get("run_id") or "") != event.run_id:
            payload = self._new_trace_payload(event)
            self.state.trace_payload = payload
        self._refresh_payload_metadata(payload, event)
        return payload

    def ensure_events_payload(self, event: RunEvent) -> dict[str, Any]:
        payload = self.state.events_payload
        if not isinstance(payload, dict) or str(payload.get("run_id") or "") != event.run_id:
            payload = self._new_events_payload(event)
            self.state.events_payload = payload
        self._refresh_payload_metadata(payload, event)
        return payload

    @staticmethod
    def _new_trace_payload(event: RunEvent) -> dict[str, Any]:
        return {
            "run_id": event.run_id,
            "request": {
                "intent": event.intent,
                "user_query": "",
            },
            "summary": {"status": "running"},
            "display_summary": None,
            "subagent_trace": [],
            "dynamic_trace": [],
            "wait_trace": [],
            "threads": [],
            "thread_tree": [],
            "event_counts": {"events": 0, "subagent_trace": 0, "dynamic_trace": 0, "wait_trace": 0},
        }

    @staticmethod
    def _new_events_payload(event: RunEvent) -> dict[str, Any]:
        return {
            "run_id": event.run_id,
            "request": {
                "intent": event.intent,
                "user_query": "",
            },
            "summary": {"status": "running"},
            "display_summary": None,
            "events": [],
            "event_counts": {"events": 0, "distinct_event_types": 0},
        }

    @staticmethod
    def _refresh_payload_metadata(payload: dict[str, Any], event: RunEvent) -> None:
        request = payload.setdefault("request", {})
        if not request.get("intent"):
            request["intent"] = event.intent
        summary = payload.setdefault("summary", {})
        if event.event_type.startswith("run_"):
            summary["status"] = ChatPresenterPayloadState._run_status(event.event_type)
            return
        summary["status"] = summary.get("status") or "running"

    @staticmethod
    def _run_status(event_type: str) -> str:
        return {
            "run_completed": "completed",
            "run_failed": "failed",
            "run_cancelled": "cancelled",
            "run_timed_out": "timed_out",
        }.get(event_type, "running")


__all__ = ["ChatPresenterPayloadState", "PresenterState"]
