from __future__ import annotations

from typing import Any

from research_system.cli.chat_trace_branch_support import trace_children_for_focus
from research_system.cli.chat_trace_grouping import group_trace_runtime_cards
from research_system.cli.chat_types import TuiSession


def build_events_rows_for_session(
    session: TuiSession,
    *,
    rows: list[dict[str, Any]] | None = None,
    respect_output_panel: bool = True,
) -> list[dict[str, Any]]:
    payload = session.events_payload
    if rows is None:
        if not isinstance(payload, dict):
            return []
        rows = [row for row in (payload.get("events") or []) if isinstance(row, dict)]
    if respect_output_panel and session.output_panel != "events":
        return rows
    trace_payload = session.trace_payload
    if not isinstance(trace_payload, dict):
        return rows
    trace_run_id = str(trace_payload.get("run_id") or "").strip()
    events_run_id = str((payload or {}).get("run_id") or "").strip()
    if not trace_run_id or trace_run_id != events_run_id:
        return rows
    focus_index = session.trace_focus_index
    if focus_index is None:
        return rows
    groups = group_trace_runtime_cards(trace_payload)
    if not (0 <= focus_index < len(groups)):
        return rows
    group = groups[focus_index]
    action_id = str(group.get("action_id") or "").strip()
    node_id = str(group.get("node_id") or "").strip()
    phase = str(group.get("phase") or "").strip()
    agent = str(group.get("agent") or "").strip()
    invocation_id = ""
    if session.trace_child_focus_index is not None:
        children = trace_children_for_focus(group, branch_focus_path=session.trace_branch_focus_path)
        if 0 <= session.trace_child_focus_index < len(children):
            invocation_id = str(children[session.trace_child_focus_index].get("invocation_id") or "").strip()

    filtered: list[dict[str, Any]] = []
    for row in rows:
        if invocation_id and str(row.get("invocation_id") or "").strip() == invocation_id:
            filtered.append(row)
            continue
        if action_id and str(row.get("action_id") or "").strip() == action_id:
            filtered.append(row)
            continue
        if (
            node_id
            and phase
            and agent
            and str(row.get("node_id") or "").strip() == node_id
            and str(row.get("phase") or "").strip() == phase
            and str(row.get("agent") or "").strip() == agent
        ):
            filtered.append(row)
    return filtered or rows
