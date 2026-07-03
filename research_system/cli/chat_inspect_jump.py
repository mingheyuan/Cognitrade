from __future__ import annotations

from research_system.cli.chat_inspect_state import (
    InspectPanelRuntime,
    events_rows,
    last_matching_event_index,
    load_events_payload_for_run,
    load_trace_payload_for_run,
)


def switch_to_focused_inspect_item(runtime: InspectPanelRuntime) -> bool:
    session = runtime.session
    if session.output_panel == "events":
        payload = session.events_payload
        focus_index = session.events_focus_index
        if not isinstance(payload, dict) or focus_index is None:
            runtime.emit_warning_update(session, "No focused event. Use F3 to focus an event first.")
            return True
        rows = events_rows(runtime)
        if not (0 <= focus_index < len(rows)):
            runtime.emit_warning_update(session, "Focused event is no longer available.")
            return True
        focused_event = rows[focus_index]
        run_id = str(payload.get("run_id") or session.current_run_id or session.state.last_run_id or "").strip()
        trace_payload = load_trace_payload_for_run(runtime, run_id)
        if not isinstance(trace_payload, dict):
            runtime.emit_warning_update(session, "Trace payload unavailable for the focused event.")
            return True
        session.output_panel = "trace"
        session.trace_child_focus_index = None
        session.trace_branch_focus_path = None
        session.trace_focus_index = None
        action_id = str(focused_event.get("action_id") or "").strip()
        node_id = str(focused_event.get("node_id") or "").strip()
        phase = str(focused_event.get("phase") or "").strip()
        agent = str(focused_event.get("agent") or "").strip()
        invocation_id = str(focused_event.get("invocation_id") or "").strip()
        from research_system.cli.chat_scene import _group_trace_runtime_cards

        groups = _group_trace_runtime_cards(trace_payload)
        for index, group in enumerate(groups):
            if action_id and str(group.get("action_id") or "").strip() == action_id:
                session.trace_focus_index = index
                break
            if (
                node_id
                and phase
                and agent
                and str(group.get("node_id") or "").strip() == node_id
                and str(group.get("phase") or "").strip() == phase
                and str(group.get("agent") or "").strip() == agent
            ):
                session.trace_focus_index = index
                break
        if session.trace_focus_index is not None and invocation_id:
            group = groups[session.trace_focus_index]
            branch_path = None
            child_index = None
            subagent_groups = list(group.get("subagent_groups") or [])
            for index, child in enumerate(subagent_groups):
                if str(child.get("invocation_id") or "").strip() != invocation_id:
                    continue
                agent_path = str(child.get("agent_path") or "").strip()
                if agent_path:
                    segments = [segment for segment in agent_path.split("/") if segment]
                    if len(segments) > 1:
                        branch_path = tuple(segments[:-1])
                child_index = index
                break
            session.trace_branch_focus_path = branch_path
            if child_index is not None:
                from research_system.cli.chat_scene import _trace_children_for_focus

                focused_children = _trace_children_for_focus(group, branch_focus_path=branch_path)
                for index, child in enumerate(focused_children):
                    if str(child.get("invocation_id") or "").strip() == invocation_id:
                        session.trace_child_focus_index = index
                        break
        session.follow_output = False
        runtime.move_output_cursor(session, absolute="start")
        runtime.invalidate()
        return True
    if session.output_panel == "trace":
        payload = session.trace_payload
        focus_index = session.trace_focus_index
        if not isinstance(payload, dict) or focus_index is None:
            runtime.emit_warning_update(session, "No focused trace task. Use F3 to focus a task first.")
            return True
        from research_system.cli.chat_scene import _group_trace_runtime_cards, _trace_children_for_focus

        groups = _group_trace_runtime_cards(payload)
        if not (0 <= focus_index < len(groups)):
            runtime.emit_warning_update(session, "Focused trace task is no longer available.")
            return True
        group = groups[focus_index]
        action_id = str(group.get("action_id") or "").strip()
        node_id = str(group.get("node_id") or "").strip()
        phase = str(group.get("phase") or "").strip()
        agent = str(group.get("agent") or "").strip()
        invocation_id = ""
        if session.trace_child_focus_index is not None:
            children = _trace_children_for_focus(group, branch_focus_path=session.trace_branch_focus_path)
            if 0 <= session.trace_child_focus_index < len(children):
                invocation_id = str(children[session.trace_child_focus_index].get("invocation_id") or "").strip()
        run_id = str(payload.get("run_id") or session.current_run_id or session.state.last_run_id or "").strip()
        events_payload = load_events_payload_for_run(runtime, run_id)
        if not isinstance(events_payload, dict):
            runtime.emit_warning_update(session, "Events payload unavailable for the focused trace task.")
            return True
        session.output_panel = "events"
        rows = events_rows(runtime)
        session.output_panel = "trace"
        match_index = last_matching_event_index(
            rows,
            invocation_id=invocation_id,
            action_id=action_id,
            node_id=node_id,
            phase=phase,
            agent=agent,
        )
        if match_index is None:
            runtime.emit_warning_update(session, "No matching event found for the focused trace task.")
            return True
        session.output_panel = "events"
        session.events_focus_index = match_index
        session.follow_output = False
        runtime.move_output_cursor(session, absolute="start")
        runtime.invalidate()
        return True
    return False
