from __future__ import annotations

from typing import Any


class SessionHistorySubagentPayloadBuilder:
    def build_payload(
        self,
        *,
        run_id: str,
        trace_payload: dict[str, Any],
        events_payload: dict[str, Any],
        invocation_id: str,
    ) -> dict[str, Any]:
        normalized_invocation_id = str(invocation_id or "").strip()
        if not normalized_invocation_id:
            raise KeyError("subagent invocation_id is required")
        trace_rows = self._matching_rows(
            trace_payload.get("subagent_trace") or [],
            invocation_id=normalized_invocation_id,
        )
        event_rows = self._matching_rows(
            events_payload.get("events") or [],
            invocation_id=normalized_invocation_id,
        )
        if not trace_rows and not event_rows:
            raise KeyError(f"subagent invocation not found: {normalized_invocation_id}")
        return {
            "run_id": run_id,
            "request": trace_payload.get("request") or {},
            "summary": trace_payload.get("summary") or {},
            "display_summary": trace_payload.get("display_summary"),
            "invocation_id": normalized_invocation_id,
            "subagent_type": self._coalesce(trace_rows, event_rows, "subagent_type"),
            "task_name": self._coalesce(trace_rows, event_rows, "task_name"),
            "agent_path": self._coalesce(trace_rows, event_rows, "agent_path"),
            "agent_label": self._coalesce(trace_rows, event_rows, "agent_label"),
            "status": self._coalesce(trace_rows, event_rows, "status"),
            "event_type": self._coalesce(trace_rows, event_rows, "event_type"),
            "action_id": self._coalesce(trace_rows, event_rows, "action_id"),
            "node_id": self._coalesce(trace_rows, event_rows, "node_id"),
            "phase": self._coalesce(trace_rows, event_rows, "phase"),
            "agent": self._coalesce(trace_rows, event_rows, "agent"),
            "objective": self._coalesce(trace_rows, event_rows, "objective"),
            "notes": self._coalesce(trace_rows, event_rows, "notes") or [],
            "trace_rows": trace_rows,
            "events": event_rows,
            "event_counts": {
                "trace_rows": len(trace_rows),
                "events": len(event_rows),
            },
        }

    @staticmethod
    def _matching_rows(rows: list[Any], *, invocation_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in rows
            if isinstance(row, dict) and str(row.get("invocation_id") or "").strip() == invocation_id
        ]

    @staticmethod
    def _coalesce(trace_rows: list[dict[str, Any]], event_rows: list[dict[str, Any]], key: str):
        for row in reversed(event_rows):
            value = row.get(key)
            if value not in (None, "", []):
                return value
        for row in reversed(trace_rows):
            value = row.get(key)
            if value not in (None, "", []):
                return value
        return None
