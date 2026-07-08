from __future__ import annotations

from typing import Any


class SessionHistoryTraceSections:
    def __init__(self, *, row_builder) -> None:
        self.row_builder = row_builder

    def subagent_trace(self, debug_payload: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(debug_payload.get("subagent_trace"), list):
            return [dict(item) for item in debug_payload["subagent_trace"] if isinstance(item, dict)]
        rows: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "")
            if event_type not in {
                "subagent_started",
                "subagent_completed",
                "subagent_failed",
                "subagent_trace",
                "thread_started",
                "thread_completed",
                "thread_failed",
                "thread_trace",
            }:
                continue
            rows.append(
                self.row_builder.subagent_trace_row(
                    event_type=event_type,
                    phase=event.get("phase"),
                    agent=event.get("agent"),
                    payload=event.get("payload") or {},
                    created_at=event.get("created_at"),
                    derive_labels=False,
                    fallback_status=False,
                )
            )
        return rows

    def dynamic_trace(self, debug_payload: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(debug_payload.get("dynamic_trace"), list):
            return [dict(item) for item in debug_payload["dynamic_trace"] if isinstance(item, dict)]
        rows: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "")
            if (
                event_type != "dynamic_action_inserted"
                and not event_type.startswith("subagent_dag_")
                and not event_type.startswith("subagent_phase_shadow_")
                and not event_type.startswith("action_phase_shadow_")
            ):
                continue
            rows.append(
                self.row_builder.dynamic_trace_row(
                    event_type=event_type,
                    phase=event.get("phase"),
                    agent=event.get("agent"),
                    payload=event.get("payload") or {},
                    created_at=event.get("created_at"),
                    default_context=False,
                )
            )
        return rows

    def action_plan_patches(self, debug_payload: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(debug_payload.get("action_plan_patches"), list):
            return [dict(item) for item in debug_payload["action_plan_patches"] if isinstance(item, dict)]
        rows: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "")
            if event_type != "action_plan_patch_applied":
                continue
            rows.append(
                self.row_builder.action_plan_patch_row(
                    event_type=event_type,
                    phase=event.get("phase"),
                    agent=event.get("agent"),
                    payload=event.get("payload") or {},
                    created_at=event.get("created_at"),
                    default_context=False,
                )
            )
        return rows

    def wait_trace(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            row = self.row_builder.wait_trace_row(
                event_type=str(event.get("event_type") or ""),
                phase=event.get("phase"),
                agent=event.get("agent"),
                message=str(event.get("message") or ""),
                payload=event.get("payload") or {},
                created_at=event.get("created_at"),
            )
            if row is not None:
                rows.append(row)
        return rows

    def event_timeline(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            rows.append(
                self.row_builder.event_timeline_row(
                    event_type=str(event.get("event_type") or ""),
                    phase=event.get("phase"),
                    agent=event.get("agent"),
                    message=str(event.get("message") or ""),
                    payload=event.get("payload") or {},
                    created_at=event.get("created_at"),
                )
            )
        return rows


__all__ = ["SessionHistoryTraceSections"]
