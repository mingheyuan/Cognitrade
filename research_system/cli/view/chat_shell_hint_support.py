from __future__ import annotations

from typing import Any, Callable

from research_system.cli.view.chat_scene_models import HintBarView
from research_system.cli.view.chat_transcript_scene import active_transcript_message_ids
from research_system.cli.state.chat_types import TuiSession


ThreadSummaryLoader = Callable[..., dict[str, Any]]


def build_hint_bar_view(
    session: TuiSession,
    *,
    current_thread_summary: ThreadSummaryLoader,
) -> HintBarView:
    if session.help_mode == "commands":
        message = "popup | Esc close | Tab complete | Enter submit"
    elif session.output_panel == "trace":
        message = trace_panel_hint(session)
    elif session.output_panel == "events":
        message = events_panel_hint(session)
    elif session.output_panel == "threads":
        message = threads_panel_hint(session)
    elif session.running:
        tail_mode = transcript_tail_mode(session)
        message = f"running | output transcript:{session.output_mode} | {tail_mode} | Ctrl+C or /stop | F1 commands | F2 trace"
    elif session.state.pending_clarification_question:
        tail_mode = transcript_tail_mode(session)
        message = f"clarification pending | output transcript:{session.output_mode} | {tail_mode} | next plain message continues the current request | F1 commands | F2 trace"
    else:
        tail_mode = transcript_tail_mode(session)
        ambient = idle_transcript_context_text(
            session,
            current_thread_summary=current_thread_summary,
        )
        message = f"ready | {ambient} | {tail_mode} | PageUp/PageDown scroll transcript | End jump live | F1 commands | F2 trace"
    return HintBarView(text=message, visible=bool(message))


def transcript_tail_mode(session: TuiSession) -> str:
    return "live-tail:on" if active_transcript_message_ids(session) else "live-tail:off"


def trace_panel_hint(session: TuiSession) -> str:
    run_id = session.current_run_id or session.state.last_run_id or "-"
    raw_mode = "raw:on" if session.trace_show_raw_sections else "raw:off"
    child_mode = "children:expanded" if session.trace_expand_child_details else "children:summary"
    child_focus = "child:all" if session.trace_child_focus_index is None else f"child:{session.trace_child_focus_index + 1}"
    inspect_mode = "inspect:all"
    if session.trace_focus_index is not None:
        inspect_mode = "inspect:task"
    if session.trace_branch_focus_path:
        inspect_mode = "inspect:branch"
    if session.trace_show_raw_sections and session.trace_child_focus_index is not None:
        inspect_mode = "inspect:focused-call"
    branch_focus = "branch:all"
    if session.trace_branch_focus_path:
        branch_focus = "branch:" + "/".join(session.trace_branch_focus_path)
    branch_collapse = f"branches:collapsed={len(session.trace_collapsed_branches)}" if session.trace_collapsed_branches else "branches:open"
    collapse_mode = "tasks:mixed"
    if session.trace_collapsed_tasks:
        collapse_mode = f"tasks:collapsed={len(session.trace_collapsed_tasks)}"
    focus_suffix = (
        f" | {raw_mode} | {inspect_mode} | {child_mode} | {child_focus} | {branch_focus} | {branch_collapse} | {collapse_mode}"
        " | F3 next task | F4 all tasks | F5 raw | F12 children"
        " | Ctrl+F3 child | Ctrl+F4 all children | Ctrl+F9 drill next | Ctrl+F10 up branch/clear call | Ctrl+F11 collapse branch | Ctrl+F12 collapse"
    )
    if session.trace_focus_index is not None:
        focus_suffix = (
            f" | {raw_mode} | {inspect_mode} | {child_mode} | focus task {session.trace_focus_index + 1}"
            f" | {child_focus} | {branch_focus} | {branch_collapse} | {collapse_mode}"
            " | F3 next | F4 all tasks | F5 raw | F12 children"
            " | Ctrl+F3 child | Ctrl+F4 all children | Ctrl+F9 drill next | Ctrl+F10 up branch/clear call | Ctrl+F11 collapse branch | Ctrl+F12 collapse"
        )
    return f"trace panel | run {run_id} | PageUp/PageDown scroll trace | F2 transcript{focus_suffix}"


def events_panel_hint(session: TuiSession) -> str:
    run_id = session.current_run_id or session.state.last_run_id or "-"
    inspect_mode = "inspect:all"
    if session.trace_focus_index is not None:
        inspect_mode = "inspect:task"
    if session.trace_branch_focus_path:
        inspect_mode = "inspect:branch"
    if session.trace_show_raw_sections and session.trace_child_focus_index is not None:
        inspect_mode = "inspect:focused-call"
    focus_suffix = " | F3 next event | F4 clear event | F9 jump trace"
    if session.events_focus_index is not None:
        focus_suffix = f" | focus event {session.events_focus_index + 1}{focus_suffix}"
    return (
        f"events panel | run {run_id} | {inspect_mode}"
        f" | PageUp/PageDown scroll events | F2 transcript | F6 next panel{focus_suffix}"
        " | /events <run_id|last> to reload"
    )


def threads_panel_hint(session: TuiSession) -> str:
    focus_suffix = " | F8 next thread | Ctrl+F8 next tree node | F9 resume"
    if session.thread_focus_index is not None:
        tree_focus = "tree:all" if session.thread_tree_focus_index is None else f"tree:{session.thread_tree_focus_index + 1}"
        focus_suffix = (
            f" | focus thread {session.thread_focus_index + 1} | {tree_focus}"
            " | F8 next thread | Ctrl+F8 next tree node | F9 resume | F10 fork | F11 archive"
        )
    return f"threads panel | scope {session.thread_panel_scope} | F6 trace | F7 next scope{focus_suffix} | /threads to change scope"


def idle_transcript_context_text(
    session: TuiSession,
    *,
    current_thread_summary: ThreadSummaryLoader,
) -> str:
    parts: list[str] = [f"output transcript:{session.output_mode}"]
    try:
        summary = current_thread_summary(settings=session.settings, state=session.state)
    except AttributeError:
        return " | ".join(parts)
    active_context_parts: list[str] = []
    current_intent = str(session.state.current_intent or "").strip()
    current_phase = str(session.current_phase or "").strip()
    run_state = str(session.run_status_label or "").strip()
    if current_intent and current_intent != "auto":
        active_context_parts.append(current_intent)
    if current_phase and current_phase != "-":
        active_context_parts.append(current_phase)
    if run_state and run_state not in {"idle", "-"}:
        active_context_parts.append(run_state)
    if active_context_parts:
        parts.append("context " + " / ".join(active_context_parts))
    thread_label = str(summary.get("thread_label") or "").strip()
    topic_title = str(summary.get("topic_title") or "").strip()
    thread_status = str(summary.get("thread_status") or "").strip()
    recommended_next_step = str(summary.get("recommended_next_step") or "").strip()
    if thread_label:
        parts.append(f"thread {thread_label}")
    if topic_title:
        parts.append(f"topic {topic_title}")
    if thread_status and thread_status != "active":
        parts.append(f"state {thread_status}")
    if recommended_next_step:
        parts.append(f"next {recommended_next_step}")
    return " | ".join(parts)

