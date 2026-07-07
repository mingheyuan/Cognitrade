from __future__ import annotations

from typing import Any

from research_system.runtime.history.session.steps.step_rows import SessionHistoryStepRows
from research_system.runtime.history.session.steps.supervisor_state import SessionHistorySupervisorState
from research_system.runtime.presentation.display import build_display_summary


class SessionHistoryStepBuilder:
    def __init__(self) -> None:
        self.supervisor_state_builder = SessionHistorySupervisorState()
        self.step_rows_builder = SessionHistoryStepRows()

    def build_payload(
        self,
        *,
        run_id: str,
        request_payload: dict[str, Any],
        result_payload: dict[str, Any],
        summary_payload: dict[str, Any],
        task_plan_payload: dict[str, Any],
        events: list[dict[str, Any]],
        threads: list[dict[str, Any]] | None = None,
        thread_tree: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        action_rows = self.action_rows(task_plan_payload)
        supervisor_state = self.supervisor_state(events)
        step_rows = self.step_rows(action_rows=action_rows, supervisor_state=supervisor_state)
        status_counts = self.status_counts(step_rows)
        return {
            "run_id": run_id,
            "request": request_payload,
            "summary": summary_payload,
            "display_summary": build_display_summary(
                result_payload=result_payload,
                summary_payload=summary_payload,
            ),
            "plan_id": task_plan_payload.get("plan_id"),
            "intent": task_plan_payload.get("intent") or request_payload.get("intent"),
            "strategy": task_plan_payload.get("strategy"),
            "entry_strategy": task_plan_payload.get("entry_strategy"),
            "entry_reason": task_plan_payload.get("entry_reason"),
            "subagent_required": task_plan_payload.get("subagent_required"),
            "dispatch_trace": dict(task_plan_payload.get("dispatch_trace") or {}),
            "notes": list(task_plan_payload.get("notes") or []),
            "threads": list(threads or []),
            "thread_tree": list(thread_tree or []),
            "steps": step_rows,
            "step_counts": {
                "total": len(step_rows),
                "active": int(status_counts.get("active") or 0),
                "completed": int(status_counts.get("completed") or 0),
                "pending": int(status_counts.get("pending") or 0),
            },
            "status_counts": status_counts,
        }

    def action_rows(self, task_plan_payload: dict[str, Any]) -> list[dict[str, Any]]:
        return self.step_rows_builder.action_rows(task_plan_payload)

    def supervisor_state(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        return self.supervisor_state_builder.supervisor_state(events)

    def event_payloads(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.supervisor_state_builder.event_payloads(events)

    def step_rows(
        self,
        *,
        action_rows: list[dict[str, Any]],
        supervisor_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return self.step_rows_builder.step_rows(
            action_rows=action_rows,
            supervisor_state=supervisor_state,
        )

    def status_counts(self, step_rows: list[dict[str, Any]]) -> dict[str, int]:
        return self.step_rows_builder.status_counts(step_rows)


__all__ = ["SessionHistoryStepBuilder"]
