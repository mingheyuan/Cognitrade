from __future__ import annotations

from research_system.cli.inspect.chat_inspect_jump import switch_to_focused_inspect_item
from research_system.cli.inspect.chat_inspect_navigation import (
    clear_trace_branch_focus,
    clear_trace_child_focus,
    clear_trace_focus,
    focus_next_trace_branch,
    focus_next_trace_child,
    focus_next_trace_task,
    toggle_focused_trace_task_collapse,
    toggle_trace_branch_collapse,
    toggle_trace_child_details,
    toggle_trace_raw_sections,
)
from research_system.cli.inspect.chat_inspect_state import (
    InspectPanelRuntime,
    build_inspect_panel_runtime,
    load_events_panel,
    toggle_output_panel,
    toggle_trace_threads_panel,
)

__all__ = [
    "InspectPanelRuntime",
    "build_inspect_panel_runtime",
    "clear_trace_branch_focus",
    "clear_trace_child_focus",
    "clear_trace_focus",
    "focus_next_trace_branch",
    "focus_next_trace_child",
    "focus_next_trace_task",
    "load_events_panel",
    "switch_to_focused_inspect_item",
    "toggle_focused_trace_task_collapse",
    "toggle_output_panel",
    "toggle_trace_branch_collapse",
    "toggle_trace_child_details",
    "toggle_trace_raw_sections",
    "toggle_trace_threads_panel",
]
