from __future__ import annotations

from typing import Any

from research_system.services.payloads.runs.run_payloads import render_trace_row_text


def group_trace_runtime_cards(payload: dict) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []

    def _trace_child_label(row: dict[str, Any]) -> str:
        explicit = str(row.get("task_name") or "").strip()
        if explicit:
            return explicit
        agent_label = str(row.get("agent_label") or "").strip()
        if agent_label:
            return agent_label
        node_id = str(row.get("node_id") or "").strip()
        subagent_type = str(row.get("subagent_type") or "").strip()
        if node_id and subagent_type:
            return f"{node_id}:{subagent_type}"
        return subagent_type or node_id or "child_agent"

    def _trace_child_group_key(row: dict[str, Any]) -> str:
        invocation_id = str(row.get("invocation_id") or "").strip()
        if invocation_id:
            return f"call:{invocation_id}"
        return _trace_child_label(row)

    def _ensure_group(
        phase: str | None,
        agent: str | None,
        node_id: str | None,
        objective: str | None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_node = str(node_id or "").strip() or "-"
        resolved_phase = str(phase or "-")
        resolved_agent = str(agent or "-")
        key = (
            str(action_id or resolved_node or "-"),
            resolved_phase,
            resolved_agent,
        )
        if key not in grouped:
            title = f"{resolved_node} | {resolved_phase} / {resolved_agent}" if resolved_node != "-" else f"{resolved_phase} / {resolved_agent}"
            task_label = resolved_node if resolved_node != "-" else f"{resolved_phase}:{resolved_agent}"
            grouped[key] = {
                "phase": resolved_phase,
                "agent": resolved_agent,
                "node_id": None if resolved_node == "-" else resolved_node,
                "action_id": str(action_id or "").strip() or None,
                "task_id": "|".join(key),
                "objective": str(objective or "").strip() or None,
                "title": title,
                "task_label": task_label,
                "status_counts": {},
                "subagents": [],
                "subagent_groups": [],
                "dynamic": [],
                "waiting": [],
            }
            order.append(key)
        elif not grouped[key].get("objective") and str(objective or "").strip():
            grouped[key]["objective"] = str(objective).strip()
        return grouped[key]

    for row in payload.get("subagent_trace") or []:
        if not isinstance(row, dict):
            continue
        group = _ensure_group(
            row.get("phase"),
            row.get("agent"),
            row.get("node_id"),
            row.get("action_objective") or row.get("objective"),
            row.get("action_id"),
        )
        status = str(row.get("status") or "unknown").strip() or "unknown"
        group["status_counts"][status] = int(group["status_counts"].get(status) or 0) + 1
        rendered_row = render_trace_row_text(row).strip()
        group["subagents"].append(rendered_row)
        child_label = _trace_child_label(row)
        child_group_key = _trace_child_group_key(row)
        child_group = next(
            (
                item for item in group["subagent_groups"]
                if str(item.get("key") or "") == child_group_key
            ),
            None,
        )
        if child_group is None:
            child_group = {
                "key": child_group_key,
                "title": child_label,
                "agent_path": str(row.get("agent_path") or "").strip() or None,
                "agent_label": str(row.get("agent_label") or "").strip() or None,
                "invocation_id": str(row.get("invocation_id") or "").strip() or None,
                "objective": str(row.get("objective") or "").strip() or None,
                "status_counts": {},
                "rows": [],
            }
            group["subagent_groups"].append(child_group)
        elif not child_group.get("objective") and str(row.get("objective") or "").strip():
            child_group["objective"] = str(row.get("objective") or "").strip()
        if not child_group.get("agent_label") and str(row.get("agent_label") or "").strip():
            child_group["agent_label"] = str(row.get("agent_label") or "").strip()
        if not child_group.get("invocation_id") and str(row.get("invocation_id") or "").strip():
            child_group["invocation_id"] = str(row.get("invocation_id") or "").strip()
        if not child_group.get("agent_path") and str(row.get("agent_path") or "").strip():
            child_group["agent_path"] = str(row.get("agent_path") or "").strip()
        child_group["status_counts"][status] = int(child_group["status_counts"].get(status) or 0) + 1
        child_group["rows"].append(rendered_row)

    for row in payload.get("dynamic_trace") or []:
        if not isinstance(row, dict):
            continue
        group = _ensure_group(
            row.get("phase"),
            row.get("agent"),
            row.get("node_id"),
            row.get("objective"),
            row.get("action_id"),
        )
        group["dynamic"].append(render_trace_row_text(row).strip())

    for row in payload.get("wait_trace") or []:
        if not isinstance(row, dict):
            continue
        group = _ensure_group(
            row.get("phase"),
            row.get("agent"),
            row.get("node_id"),
            row.get("objective"),
            row.get("action_id"),
        )
        group["waiting"].append(render_trace_row_text(row).strip())

    return [grouped[key] for key in order]

