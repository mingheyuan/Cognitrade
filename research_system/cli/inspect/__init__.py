from __future__ import annotations

from importlib import import_module

__all__ = [
    "InspectPanelRuntime",
    "build_inspect_panel_runtime",
    "events_rows",
    "load_events_panel",
    "load_events_payload_for_run",
    "load_trace_payload_for_run",
    "sync_events_focus_with_current_slice",
    "toggle_output_panel",
    "toggle_trace_threads_panel",
    "trace_branch_paths",
    "trace_child_count",
    "trace_groups",
    "trace_task_count",
]

_EXPORTS = {
    "InspectPanelRuntime": ("research_system.cli.inspect.chat_inspect_state", "InspectPanelRuntime"),
    "build_inspect_panel_runtime": ("research_system.cli.inspect.chat_inspect_state", "build_inspect_panel_runtime"),
    "events_rows": ("research_system.cli.inspect.chat_inspect_state", "events_rows"),
    "load_events_panel": ("research_system.cli.inspect.chat_inspect_state", "load_events_panel"),
    "load_events_payload_for_run": ("research_system.cli.inspect.chat_inspect_state", "load_events_payload_for_run"),
    "load_trace_payload_for_run": ("research_system.cli.inspect.chat_inspect_state", "load_trace_payload_for_run"),
    "sync_events_focus_with_current_slice": (
        "research_system.cli.inspect.chat_inspect_state",
        "sync_events_focus_with_current_slice",
    ),
    "toggle_output_panel": ("research_system.cli.inspect.chat_inspect_state", "toggle_output_panel"),
    "toggle_trace_threads_panel": ("research_system.cli.inspect.chat_inspect_state", "toggle_trace_threads_panel"),
    "trace_branch_paths": ("research_system.cli.inspect.chat_inspect_state", "trace_branch_paths"),
    "trace_child_count": ("research_system.cli.inspect.chat_inspect_state", "trace_child_count"),
    "trace_groups": ("research_system.cli.inspect.chat_inspect_state", "trace_groups"),
    "trace_task_count": ("research_system.cli.inspect.chat_inspect_state", "trace_task_count"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
