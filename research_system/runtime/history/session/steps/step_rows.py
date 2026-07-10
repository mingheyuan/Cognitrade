from __future__ import annotations

from typing import Any


class SessionHistoryStepRows:
    def action_rows(self, task_plan_payload: dict[str, Any]) -> list[dict[str, Any]]:
        actions = task_plan_payload.get("actions") if isinstance(task_plan_payload.get("actions"), list) else []
        if not actions and isinstance(task_plan_payload.get("nodes"), list):
            actions = [
                {
                    "action_id": (node.get("metadata") or {}).get("action_id"),
                    "node_id": node.get("node_id"),
                    "phase": (node.get("metadata") or {}).get("phase") or node.get("capability"),
                    "agent": node.get("executor"),
                    "objective": node.get("objective"),
                    "status": node.get("status"),
                    "depends_on": node.get("depends_on") or [],
                    "tags": node.get("tags") or [],
                }
                for node in task_plan_payload.get("nodes") or []
                if isinstance(node, dict) and node.get("node_kind") != "compound"
            ]
        return [dict(row) for row in actions if isinstance(row, dict)]

    def step_rows(
        self,
        *,
        action_rows: list[dict[str, Any]],
        supervisor_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        next_actions_by_node_id = dict(supervisor_state.get("next_actions_by_node_id") or {})
        completed_action_ids = set(supervisor_state.get("completed_action_ids") or set())
        skipped_action_ids = set(supervisor_state.get("skipped_action_ids") or set())
        latest_supervisor_note = supervisor_state.get("latest_supervisor_note")
        rows: list[dict[str, Any]] = []
        for index, row in enumerate(action_rows, start=1):
            node_id = str(row.get("node_id") or "").strip()
            action_id = str(row.get("action_id") or "").strip()
            status = str(row.get("status") or "").strip() or "pending"
            if action_id and action_id in skipped_action_ids:
                status = "skipped"
            elif action_id and action_id in completed_action_ids:
                status = "completed"
            elif node_id and node_id in next_actions_by_node_id:
                status = "active"
            next_action_payload = next_actions_by_node_id.get(node_id) or {}
            rows.append(
                {
                    "step_index": index,
                    "action_id": row.get("action_id"),
                    "node_id": row.get("node_id"),
                    "phase": row.get("phase"),
                    "agent": row.get("agent"),
                    "objective": row.get("objective"),
                    "status": status,
                    "tags": list(row.get("tags") or []),
                    "inputs": dict(row.get("inputs") or {}),
                    "active_from_supervisor": bool(node_id and node_id in next_actions_by_node_id),
                    "supervisor_note": latest_supervisor_note if node_id and node_id in next_actions_by_node_id else None,
                    "next_action_payload": next_action_payload or None,
                }
            )
        return rows

    def status_counts(self, step_rows: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in step_rows:
            status = str(row.get("status") or "pending")
            counts[status] = int(counts.get(status) or 0) + 1
        return counts


__all__ = ["SessionHistoryStepRows"]
