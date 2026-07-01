from __future__ import annotations

from pathlib import Path
from typing import Any

from research_system.runtime.display import build_display_summary
from research_system.runtime.jsonl import read_json, read_jsonl, write_json


class SessionHistory:
    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root

    def replay(self) -> dict[str, Any]:
        result_payload = read_json(self.run_root / "result.json")
        summary_payload = read_json(self.run_root / "summary.json")
        return {
            "run_id": self.run_root.name,
            "request": read_json(self.run_root / "request.json"),
            "events": read_jsonl(self.run_root / "events.jsonl"),
            "blackboard": read_jsonl(self.run_root / "blackboard.jsonl"),
            "result": result_payload,
            "summary": summary_payload,
            "display_summary": build_display_summary(
                result_payload=result_payload,
                summary_payload=summary_payload,
            ),
        }

    def trace(self) -> dict[str, Any]:
        request_payload = read_json(self.run_root / "request.json") or {}
        result_payload = read_json(self.run_root / "result.json") or {}
        summary_payload = read_json(self.run_root / "summary.json") or {}
        events = read_jsonl(self.run_root / "events.jsonl")
        debug_payload = self._debug_payload(result_payload)
        subagent_trace = self._subagent_trace(debug_payload, events)
        dynamic_trace = self._dynamic_trace(debug_payload, events)
        wait_trace = self._wait_trace(events)
        return {
            "run_id": self.run_root.name,
            "request": request_payload,
            "summary": summary_payload,
            "display_summary": build_display_summary(
                result_payload=result_payload,
                summary_payload=summary_payload,
            ),
            "subagent_trace": subagent_trace,
            "dynamic_trace": dynamic_trace,
            "wait_trace": wait_trace,
            "event_counts": {
                "events": len(events),
                "subagent_trace": len(subagent_trace),
                "dynamic_trace": len(dynamic_trace),
                "wait_trace": len(wait_trace),
            },
        }

    def write_result(self, result: dict[str, Any], summary: dict[str, Any]) -> None:
        write_json(self.run_root / "result.json", result)
        write_json(self.run_root / "summary.json", summary)

    def _debug_payload(self, result_payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result_payload, dict):
            return {}
        report_payload = result_payload.get("report")
        if isinstance(report_payload, dict):
            debug_payload = report_payload.get("debug")
            if isinstance(debug_payload, dict):
                return debug_payload
        debug_payload = result_payload.get("debug")
        if isinstance(debug_payload, dict):
            return debug_payload
        return {}

    def _subagent_trace(self, debug_payload: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(debug_payload.get("subagent_trace"), list):
            return [dict(item) for item in debug_payload["subagent_trace"] if isinstance(item, dict)]
        rows: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            if str(event.get("event_type") or "") not in {
                "subagent_started",
                "subagent_completed",
                "subagent_failed",
                "subagent_trace",
            }:
                continue
            payload = dict(event.get("payload") or {})
            row = {
                "event_type": str(event.get("event_type") or ""),
                "phase": event.get("phase"),
                "agent": event.get("agent"),
                "subagent_type": payload.get("subagent_type"),
                "action_id": payload.get("action_id"),
                "node_id": payload.get("node_id"),
                "action_objective": payload.get("action_objective"),
                "status": payload.get("status"),
                "objective": payload.get("objective"),
                "notes": payload.get("notes") or [],
                "created_at": event.get("created_at"),
            }
            rows.append(row)
        return rows

    def _dynamic_trace(self, debug_payload: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(debug_payload.get("dynamic_trace"), list):
            return [dict(item) for item in debug_payload["dynamic_trace"] if isinstance(item, dict)]
        rows: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            if str(event.get("event_type") or "") != "dynamic_action_inserted":
                continue
            payload = dict(event.get("payload") or {})
            payload.setdefault("event", "dynamic_action_inserted")
            payload.setdefault("created_at", event.get("created_at"))
            rows.append(payload)
        return rows

    def _wait_trace(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "")
            message = str(event.get("message") or "")
            if event_type == "phase_started" and message == "Entered waiting_external state":
                payload = dict(event.get("payload") or {})
                rows.append(
                    {
                        "event": "waiting_external_started",
                        "action_id": payload.get("action_id"),
                        "node_id": payload.get("node_id"),
                        "phase": event.get("phase"),
                        "agent": event.get("agent"),
                        "objective": payload.get("objective"),
                        "wait_timeout_seconds": payload.get("wait_timeout_seconds"),
                        "created_at": event.get("created_at"),
                    }
                )
                continue
            if event_type == "run_timed_out" and "External wait timed out" in message:
                rows.append(
                    {
                        "event": "waiting_external_timed_out",
                        "created_at": event.get("created_at"),
                    }
                )
                continue
            if event_type == "run_cancelled" and "waiting" in message.lower():
                rows.append(
                    {
                        "event": "waiting_external_cancelled",
                        "created_at": event.get("created_at"),
                    }
                )
        return rows
