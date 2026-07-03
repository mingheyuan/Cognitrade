from __future__ import annotations

from typing import Any

from research_system.cli.view.chat_trace_branch_support import trace_children_for_focus
from research_system.cli.view.chat_trace_events_rows import build_events_rows_for_session
from research_system.cli.view.chat_trace_grouping import group_trace_runtime_cards
from research_system.cli.state.chat_types import TuiSession


def build_linked_event_for_trace_session(session: TuiSession) -> dict[str, Any] | None:
    if not (isinstance(session.trace_payload, dict) and isinstance(session.events_payload, dict)):
        return None
    trace_run_id = str(session.trace_payload.get("run_id") or "").strip()
    events_run_id = str(session.events_payload.get("run_id") or "").strip()
    if not trace_run_id or trace_run_id != events_run_id:
        return None
    focus_index = session.trace_focus_index
    if focus_index is None:
        return None
    groups = group_trace_runtime_cards(session.trace_payload)
    if not (0 <= focus_index < len(groups)):
        return None
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
    rows = build_events_rows_for_session(session, respect_output_panel=False)
    if not rows:
        return None
    match_index = None
    if invocation_id:
        for index, row in enumerate(rows):
            if str(row.get("invocation_id") or "").strip() == invocation_id:
                match_index = index
    if match_index is None and action_id:
        for index, row in enumerate(rows):
            if str(row.get("action_id") or "").strip() == action_id:
                match_index = index
    if match_index is None:
        for index, row in enumerate(rows):
            if (
                str(row.get("node_id") or "").strip() == node_id
                and str(row.get("phase") or "").strip() == phase
                and str(row.get("agent") or "").strip() == agent
            ):
                match_index = index
    if match_index is None:
        return None
    return rows[match_index]


def build_linked_trace_for_events_session(session: TuiSession) -> dict[str, Any] | None:
    if session.output_panel != "events":
        return None
    if not (isinstance(session.trace_payload, dict) and isinstance(session.events_payload, dict)):
        return None
    trace_run_id = str(session.trace_payload.get("run_id") or "").strip()
    events_run_id = str(session.events_payload.get("run_id") or "").strip()
    if not trace_run_id or trace_run_id != events_run_id:
        return None
    focus_index = session.trace_focus_index
    if focus_index is None:
        return None
    groups = group_trace_runtime_cards(session.trace_payload)
    if not (0 <= focus_index < len(groups)):
        return None
    group = groups[focus_index]
    linked: dict[str, Any] = {
        "task": group.get("task_label") or "-",
        "phase": group.get("phase") or "-",
        "agent": group.get("agent") or "-",
    }
    if group.get("objective"):
        linked["objective"] = group.get("objective")
    if session.trace_branch_focus_path:
        linked["branch"] = "/".join(session.trace_branch_focus_path)
    if session.trace_child_focus_index is not None:
        children = trace_children_for_focus(group, branch_focus_path=session.trace_branch_focus_path)
        if 0 <= session.trace_child_focus_index < len(children):
            child = children[session.trace_child_focus_index]
            linked["work_unit"] = child.get("task_name") or child.get("title") or child.get("agent_label") or "-"
            if child.get("invocation_id"):
                linked["call_id"] = child.get("invocation_id")
    return linked


def linked_event_summary_for_trace_focus(session: TuiSession) -> tuple[bool, str]:
    linked_event = build_linked_event_for_trace_session(session)
    if not isinstance(linked_event, dict):
        return False, ""
    event_type = str(linked_event.get("event_type") or "").strip() or "event"
    status = str(linked_event.get("status") or "").strip()
    summary = event_type
    if status:
        summary = f"{summary}:{status}"
    return True, summary

