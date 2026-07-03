from __future__ import annotations

from research_system.cli.view.chat_scene_models import StatusBarItem
from research_system.cli.state.chat_types import TuiSession


def output_mode_label(session: TuiSession) -> str:
    if session.output_panel == "trace":
        return "trace"
    if session.output_panel == "events":
        return "events"
    if session.output_panel == "threads":
        return f"threads:{session.thread_panel_scope}"
    return f"transcript:{session.output_mode}"


def build_status_bar_items(session: TuiSession) -> list[StatusBarItem]:
    intent = session.state.current_intent or "auto"
    run_id = session.current_run_id or session.state.last_run_id or "-"
    thread_short = (session.state.thread_id or "-")[-8:]
    mode = "running" if session.running else session.run_status_label
    clarify = "pending" if session.state.pending_clarification_question else "clear"
    inspect_scope = inspect_scope_label(session)
    return [
        StatusBarItem("mode", "codex"),
        StatusBarItem("thread", thread_short),
        StatusBarItem("intent", intent),
        StatusBarItem("run", run_id),
        StatusBarItem("phase", session.current_phase),
        StatusBarItem("gate", session.current_gate),
        StatusBarItem("output", output_mode_label(session)),
        StatusBarItem("inspect", inspect_scope),
        StatusBarItem("clarify", clarify),
        StatusBarItem("thinking", "on" if session.state.thinking_enabled else "off"),
        StatusBarItem("events", session.state.events_mode),
        StatusBarItem("state", mode),
    ]


def inspect_scope_label(session: TuiSession) -> str:
    inspect_scope = "-"
    if session.output_panel == "trace":
        inspect_scope = "all"
        if session.trace_focus_index is not None:
            inspect_scope = "task"
        if session.trace_branch_focus_path:
            inspect_scope = "branch"
        if session.trace_show_raw_sections and session.trace_child_focus_index is not None:
            inspect_scope = "call"
    elif session.output_panel == "events":
        inspect_scope = "all"
        if session.trace_focus_index is not None:
            inspect_scope = "task"
        if session.trace_branch_focus_path:
            inspect_scope = "branch"
        if session.trace_show_raw_sections and session.trace_child_focus_index is not None:
            inspect_scope = "call"
    return inspect_scope

