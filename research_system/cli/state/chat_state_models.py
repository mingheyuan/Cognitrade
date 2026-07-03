from __future__ import annotations

import threading
from dataclasses import dataclass, field

from prompt_toolkit.application import Application
from prompt_toolkit.layout import Float, Window
from prompt_toolkit.layout.containers import HSplit
from prompt_toolkit.widgets import TextArea


@dataclass
class ChatSessionState:
    thread_id: str | None = None
    thread_source: str | None = None
    parent_thread_id: str | None = None
    current_intent: str | None = None
    review_target_run_id: str | None = None
    last_run_id: str | None = None
    thinking_enabled: bool = True
    events_mode: str = "compact"
    last_user_query: str | None = None
    last_result_intent: str | None = None
    last_result_summary: str | None = None
    last_result_conclusion: str | None = None
    last_result_follow_up: str | None = None
    pending_clarification_query: str | None = None
    pending_clarification_intent: str | None = None
    pending_clarification_run_id: str | None = None
    pending_clarification_question: str | None = None


@dataclass
class ShellViewState:
    current_run_id: str | None = None
    running: bool = False
    stop_requested: bool = False
    current_phase: str = "-"
    current_gate: str = "pending"
    run_status_label: str = "idle"
    commands_popup_visible: bool = False
    focus_target: str = "input"
    help_mode: str = "hidden"
    input_mode: str = "insert"
    output_mode: str = "live"
    output_panel: str = "transcript"
    thread_panel_scope: str = "active"
    thread_focus_index: int | None = None
    thread_tree_focus_index: int | None = None
    answer_stream_open: bool = False
    follow_output: bool = True
    trace_payload: dict | None = None
    events_payload: dict | None = None
    subagent_payload: dict | None = None
    events_focus_index: int | None = None
    trace_focus_index: int | None = None
    trace_child_focus_index: int | None = None
    trace_branch_focus_path: tuple[str, ...] | None = None
    trace_collapsed_branches: set[tuple[str, ...]] = field(default_factory=set)
    trace_show_raw_sections: bool = True
    trace_expand_child_details: bool = True
    trace_collapsed_tasks: set[str] = field(default_factory=set)
    active_message_ids: dict[str, str] = field(default_factory=dict)
    closed_message_ids: set[str] = field(default_factory=set)


@dataclass
class ShellUiState:
    app: Application | None = None
    output_area: TextArea | None = None
    main_stack: HSplit | None = None
    input_area: TextArea | None = None
    commands_popup: TextArea | None = None
    commands_popup_float: Float | None = None
    status_bar: Window | None = None
    hint_bar: Window | None = None
    worker: threading.Thread | None = None
    last_view_sync_signature: tuple | None = None
    last_rendered_output_text: str = ""
    last_focus_target: str | None = None
    pending_stream_update: object | None = None


__all__ = ["ChatSessionState", "ShellViewState", "ShellUiState"]
