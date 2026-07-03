from __future__ import annotations

from pathlib import Path
from typing import Any

from research_system.runtime.history.session.steps.step_builder import SessionHistoryStepBuilder
from research_system.runtime.history.session.trace.trace_rebuilder import SessionHistoryTraceRebuilder
from research_system.runtime.presentation.display import build_display_summary
from research_system.runtime.storage.jsonl import read_json, read_jsonl, write_json


class SessionHistory:
    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root
        self.trace_rebuilder = SessionHistoryTraceRebuilder()
        self.step_builder = SessionHistoryStepBuilder()

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
        debug_payload = self.trace_rebuilder.debug_payload(result_payload)
        subagent_trace = self.trace_rebuilder.subagent_trace(debug_payload, events)
        dynamic_trace = self.trace_rebuilder.dynamic_trace(debug_payload, events)
        wait_trace = self.trace_rebuilder.wait_trace(events)
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

    def events(self) -> dict[str, Any]:
        request_payload = read_json(self.run_root / "request.json") or {}
        result_payload = read_json(self.run_root / "result.json") or {}
        summary_payload = read_json(self.run_root / "summary.json") or {}
        events = read_jsonl(self.run_root / "events.jsonl")
        timeline = self.trace_rebuilder.event_timeline(events)
        return {
            "run_id": self.run_root.name,
            "request": request_payload,
            "summary": summary_payload,
            "display_summary": build_display_summary(
                result_payload=result_payload,
                summary_payload=summary_payload,
            ),
            "events": timeline,
            "event_counts": {
                "events": len(timeline),
                "distinct_event_types": len({str(row.get("event_type") or "") for row in timeline}),
            },
        }

    def steps(self) -> dict[str, Any]:
        request_payload = read_json(self.run_root / "request.json") or {}
        result_payload = read_json(self.run_root / "result.json") or {}
        summary_payload = read_json(self.run_root / "summary.json") or {}
        task_plan_payload = read_json(self.run_root / "task_plan.json") or {}
        events = read_jsonl(self.run_root / "events.jsonl")
        return self.step_builder.build_payload(
            run_id=self.run_root.name,
            request_payload=request_payload,
            result_payload=result_payload,
            summary_payload=summary_payload,
            task_plan_payload=task_plan_payload,
            events=events,
        )

    def subagent(self, invocation_id: str) -> dict[str, Any]:
        trace_payload = self.trace()
        events_payload = self.events()
        return self.trace_rebuilder.build_subagent_payload(
            run_id=self.run_root.name,
            trace_payload=trace_payload,
            events_payload=events_payload,
            invocation_id=invocation_id,
        )

    def write_result(self, result: dict[str, Any], summary: dict[str, Any]) -> None:
        write_json(self.run_root / "result.json", result)
        write_json(self.run_root / "summary.json", summary)


__all__ = ["SessionHistory"]
