from __future__ import annotations

from research_system.cli.chat_types import TuiSession


def events_rows_for_session(session: TuiSession) -> list[dict]:
    payload = session.events_payload
    if session.output_panel != "events" or not isinstance(payload, dict):
        return []
    from research_system.cli.chat_scene import build_events_rows_for_session

    return build_events_rows_for_session(session)


def last_matching_event_index(
    rows: list[dict],
    *,
    invocation_id: str = "",
    action_id: str = "",
    node_id: str = "",
    phase: str = "",
    agent: str = "",
) -> int | None:
    match_index = None
    if invocation_id:
        for index, row in enumerate(rows):
            if str(row.get("invocation_id") or "").strip() == invocation_id:
                match_index = index
        if match_index is not None:
            return match_index
    if action_id:
        for index, row in enumerate(rows):
            if str(row.get("action_id") or "").strip() == action_id:
                match_index = index
        if match_index is not None:
            return match_index
    for index, row in enumerate(rows):
        if (
            str(row.get("node_id") or "").strip() == node_id
            and str(row.get("phase") or "").strip() == phase
            and str(row.get("agent") or "").strip() == agent
        ):
            match_index = index
    return match_index


def trace_task_count_for_session(session: TuiSession) -> int:
    payload = session.trace_payload
    if not isinstance(payload, dict):
        return 0
    keys: set[tuple[str, str, str]] = set()
    for section_name in ("subagent_trace", "dynamic_trace", "wait_trace"):
        rows = payload.get(section_name) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            node_id = str(row.get("node_id") or "").strip() or "-"
            phase = str(row.get("phase") or "-")
            agent = str(row.get("agent") or "-")
            action_id = str(row.get("action_id") or node_id or "-")
            keys.add((action_id, phase, agent))
    return len(keys)


def trace_groups_for_session(session: TuiSession) -> list[dict]:
    payload = session.trace_payload
    if session.output_panel != "trace" or not isinstance(payload, dict):
        return []
    from research_system.cli.chat_scene import _group_trace_runtime_cards

    return _group_trace_runtime_cards(payload)


def trace_child_count_for_session(session: TuiSession) -> int:
    if session.output_panel != "trace" or not isinstance(session.trace_payload, dict):
        return 0
    focus_index = session.trace_focus_index
    if focus_index is None:
        return 0
    from research_system.cli.chat_scene import _group_trace_runtime_cards, _trace_children_for_focus

    groups = _group_trace_runtime_cards(session.trace_payload)
    if not (0 <= focus_index < len(groups)):
        return 0
    subagent_groups = _trace_children_for_focus(
        groups[focus_index],
        branch_focus_path=session.trace_branch_focus_path,
    )
    return len(subagent_groups) if isinstance(subagent_groups, list) else 0


def trace_branch_paths_for_session(session: TuiSession) -> list[tuple[str, ...]]:
    if session.output_panel != "trace" or not isinstance(session.trace_payload, dict):
        return []
    focus_index = session.trace_focus_index
    if focus_index is None:
        return []
    from research_system.cli.chat_scene import _collect_trace_child_branch_paths, _group_trace_runtime_cards

    groups = _group_trace_runtime_cards(session.trace_payload)
    if not (0 <= focus_index < len(groups)):
        return []
    subagent_groups = groups[focus_index].get("subagent_groups") or []
    if not isinstance(subagent_groups, list):
        return []
    return _collect_trace_child_branch_paths(subagent_groups)
