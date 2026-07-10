from __future__ import annotations

from typing import Any

from research_system.runtime.history.session.steps.step_rows import SessionHistoryStepRows
from research_system.runtime.presentation.display import build_display_summary


class SessionHistoryStepBuilder:
    def __init__(self) -> None:
        self.step_rows_builder = SessionHistoryStepRows()

    def build_payload(
        self,
        *,
        run_id: str,
        request_payload: dict[str, Any],
        result_payload: dict[str, Any],
        summary_payload: dict[str, Any],
        execution_graph_payload: dict[str, Any],
        events: list[dict[str, Any]],
        threads: list[dict[str, Any]] | None = None,
        thread_tree: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        del events
        step_rows = self.step_rows_builder.step_rows(execution_graph_payload)
        status_counts = self.step_rows_builder.status_counts(step_rows)
        metadata = execution_graph_payload.get("metadata") or {}
        return {
            "run_id": run_id,
            "request": request_payload,
            "summary": summary_payload,
            "display_summary": build_display_summary(
                result_payload=result_payload,
                summary_payload=summary_payload,
            ),
            "graph_id": execution_graph_payload.get("graph_id"),
            "domain_id": execution_graph_payload.get("domain_id"),
            "intent": metadata.get("intent") or request_payload.get("intent"),
            "strategy": execution_graph_payload.get("strategy"),
            "revision": execution_graph_payload.get("revision"),
            "notes": list(execution_graph_payload.get("notes") or []),
            "threads": list(threads or []),
            "thread_tree": list(thread_tree or []),
            "steps": step_rows,
            "step_counts": {
                "total": len(step_rows),
                "active": int(status_counts.get("running") or 0),
                "completed": int(status_counts.get("completed") or 0),
                "pending": int(status_counts.get("pending") or 0),
            },
            "status_counts": status_counts,
        }


__all__ = ["SessionHistoryStepBuilder"]
