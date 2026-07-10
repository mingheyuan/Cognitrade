from __future__ import annotations

from typing import Any


class SessionHistoryStepRows:
    def step_rows(self, execution_graph: dict[str, Any]) -> list[dict[str, Any]]:
        nodes = execution_graph.get("nodes")
        if not isinstance(nodes, list):
            return []
        rows: list[dict[str, Any]] = []
        for index, node in enumerate(nodes, start=1):
            if not isinstance(node, dict):
                continue
            rows.append(
                {
                    "step_index": index,
                    "node_id": node.get("node_id"),
                    "node_kind": node.get("node_kind"),
                    "capability": node.get("capability"),
                    "executor": node.get("executor"),
                    "objective": node.get("objective"),
                    "status": str(node.get("status") or "pending"),
                    "depends_on": list(node.get("depends_on") or []),
                    "tags": list(node.get("tags") or []),
                    "input_artifact_types": list(node.get("input_artifact_types") or []),
                    "output_artifact_types": list(node.get("output_artifact_types") or []),
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
