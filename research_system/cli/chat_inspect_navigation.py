from __future__ import annotations

from research_system.cli.chat_inspect_state import (
    InspectPanelRuntime,
    events_count,
    sync_events_focus_with_current_slice,
    trace_branch_paths,
    trace_child_count,
    trace_groups,
    trace_task_count,
)


def focus_next_trace_task(runtime: InspectPanelRuntime) -> None:
    session = runtime.session
    if session.output_panel == "events":
        event_count = events_count(runtime)
        if event_count <= 0:
            session.events_focus_index = None
            runtime.invalidate()
            return
        current_index = session.events_focus_index
        if current_index is None or current_index >= event_count - 1:
            session.events_focus_index = 0
        else:
            session.events_focus_index = current_index + 1
        session.follow_output = False
        runtime.move_output_cursor(session, absolute="start")
        return
    if session.output_panel != "trace":
        return
    task_count = trace_task_count(runtime)
    if task_count <= 0:
        session.trace_focus_index = None
        runtime.invalidate()
        return
    current_index = session.trace_focus_index
    if current_index is None or current_index >= task_count - 1:
        session.trace_focus_index = 0
    else:
        session.trace_focus_index = current_index + 1
    session.trace_child_focus_index = None
    session.trace_branch_focus_path = None
    sync_events_focus_with_current_slice(runtime, reset_to_first=True)
    session.follow_output = False
    runtime.move_output_cursor(session, absolute="start")


def clear_trace_focus(runtime: InspectPanelRuntime) -> None:
    session = runtime.session
    if session.output_panel == "events":
        session.events_focus_index = None
        session.follow_output = False
        runtime.move_output_cursor(session, absolute="start")
        return
    if session.output_panel != "trace":
        return
    session.trace_focus_index = None
    session.trace_child_focus_index = None
    session.trace_branch_focus_path = None
    sync_events_focus_with_current_slice(runtime, reset_to_first=True)
    session.follow_output = False
    runtime.move_output_cursor(session, absolute="start")


def focus_next_trace_child(runtime: InspectPanelRuntime) -> None:
    session = runtime.session
    if session.output_panel != "trace":
        return
    child_count = trace_child_count(runtime)
    if child_count <= 0:
        session.trace_child_focus_index = None
        runtime.invalidate()
        return
    current_index = session.trace_child_focus_index
    if current_index is None or current_index >= child_count - 1:
        session.trace_child_focus_index = 0
    else:
        session.trace_child_focus_index = current_index + 1
    sync_events_focus_with_current_slice(runtime, reset_to_first=True)
    session.follow_output = False
    runtime.move_output_cursor(session, absolute="start")


def clear_trace_child_focus(runtime: InspectPanelRuntime) -> None:
    if runtime.session.output_panel != "trace":
        return
    runtime.session.trace_child_focus_index = None
    sync_events_focus_with_current_slice(runtime, reset_to_first=True)
    runtime.session.follow_output = False
    runtime.move_output_cursor(runtime.session, absolute="start")


def focus_next_trace_branch(runtime: InspectPanelRuntime) -> None:
    session = runtime.session
    if session.output_panel != "trace":
        return
    branch_paths = trace_branch_paths(runtime)
    if not branch_paths:
        session.trace_branch_focus_path = None
        session.trace_child_focus_index = None
        runtime.invalidate()
        return
    from research_system.cli.chat_scene import (
        _branch_has_nested_children,
        _group_trace_runtime_cards,
        _next_trace_branch_focus_path,
        _trace_children_for_focus,
    )

    focus_index = session.trace_focus_index
    current_branch_focus_path = session.trace_branch_focus_path
    if current_branch_focus_path is not None and focus_index is not None and isinstance(session.trace_payload, dict):
        groups = _group_trace_runtime_cards(session.trace_payload)
        if 0 <= focus_index < len(groups):
            current_branch_children = _trace_children_for_focus(
                groups[focus_index],
                branch_focus_path=current_branch_focus_path,
            )
            if current_branch_children and not _branch_has_nested_children(
                groups[focus_index],
                current_branch_focus_path,
            ):
                current_child_index = session.trace_child_focus_index
                if current_child_index is None:
                    session.trace_child_focus_index = 0
                    sync_events_focus_with_current_slice(runtime, reset_to_first=True)
                    session.follow_output = False
                    runtime.move_output_cursor(session, absolute="start")
                    return
                if current_child_index < len(current_branch_children) - 1:
                    session.trace_child_focus_index = current_child_index + 1
                    sync_events_focus_with_current_slice(runtime, reset_to_first=True)
                    session.follow_output = False
                    runtime.move_output_cursor(session, absolute="start")
                    return

    session.trace_branch_focus_path = _next_trace_branch_focus_path(branch_paths, session.trace_branch_focus_path)
    session.trace_child_focus_index = None
    if session.trace_branch_focus_path is not None and focus_index is not None and isinstance(session.trace_payload, dict):
        groups = _group_trace_runtime_cards(session.trace_payload)
        if 0 <= focus_index < len(groups):
            branch_children = _trace_children_for_focus(
                groups[focus_index],
                branch_focus_path=session.trace_branch_focus_path,
            )
            if branch_children and not _branch_has_nested_children(groups[focus_index], session.trace_branch_focus_path):
                session.trace_child_focus_index = 0
    sync_events_focus_with_current_slice(runtime, reset_to_first=True)
    session.follow_output = False
    runtime.move_output_cursor(session, absolute="start")


def clear_trace_branch_focus(runtime: InspectPanelRuntime) -> None:
    session = runtime.session
    if session.output_panel != "trace":
        return
    if session.trace_child_focus_index is not None:
        session.trace_child_focus_index = None
        sync_events_focus_with_current_slice(runtime, reset_to_first=True)
        session.follow_output = False
        runtime.move_output_cursor(session, absolute="start")
        return
    if session.trace_branch_focus_path is not None:
        branch_paths = trace_branch_paths(runtime)
        from research_system.cli.chat_scene import _previous_trace_branch_focus_path

        session.trace_branch_focus_path = _previous_trace_branch_focus_path(branch_paths, session.trace_branch_focus_path)
    else:
        session.trace_branch_focus_path = None
    session.trace_child_focus_index = None
    sync_events_focus_with_current_slice(runtime, reset_to_first=True)
    session.follow_output = False
    runtime.move_output_cursor(session, absolute="start")


def toggle_trace_branch_collapse(runtime: InspectPanelRuntime) -> None:
    session = runtime.session
    if session.output_panel != "trace":
        return
    branch_path = session.trace_branch_focus_path
    if not branch_path:
        runtime.emit_warning_update(session, "No focused trace branch. Use Ctrl+F9 to focus a branch first.")
        return
    collapsed = session.trace_collapsed_branches
    if branch_path in collapsed:
        collapsed.remove(branch_path)
    else:
        collapsed.add(branch_path)
    session.trace_collapsed_branches = collapsed
    session.follow_output = False
    runtime.invalidate()


def toggle_trace_raw_sections(runtime: InspectPanelRuntime) -> None:
    if runtime.session.output_panel != "trace":
        return
    runtime.session.trace_show_raw_sections = not runtime.session.trace_show_raw_sections
    runtime.session.follow_output = False
    runtime.invalidate()


def toggle_trace_child_details(runtime: InspectPanelRuntime) -> None:
    if runtime.session.output_panel != "trace":
        return
    runtime.session.trace_expand_child_details = not runtime.session.trace_expand_child_details
    runtime.session.follow_output = False
    runtime.invalidate()


def toggle_focused_trace_task_collapse(runtime: InspectPanelRuntime) -> None:
    session = runtime.session
    if session.output_panel != "trace":
        return
    focus_index = session.trace_focus_index
    if focus_index is None:
        runtime.emit_warning_update(session, "No focused trace task. Use F3 to focus a task first.")
        return
    groups = trace_groups(runtime)
    if not (0 <= focus_index < len(groups)):
        runtime.emit_warning_update(session, "Focused trace task is no longer available.")
        return
    task_id = str(groups[focus_index].get("task_id") or "").strip()
    if not task_id:
        runtime.emit_warning_update(session, "Focused trace task is missing a stable task id.")
        return
    collapsed = set(session.trace_collapsed_tasks)
    if task_id in collapsed:
        collapsed.remove(task_id)
    else:
        collapsed.add(task_id)
    session.trace_collapsed_tasks = collapsed
    session.follow_output = False
    runtime.invalidate()
