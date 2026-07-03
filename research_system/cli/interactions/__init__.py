from __future__ import annotations

from importlib import import_module

__all__ = [
    "emit_input_update",
    "emit_meta_update",
    "emit_warning_update",
    "request_stop",
    "submit_tui_input",
    "toggle_commands_popup",
    "write_tui_update",
]

_EXPORTS = {
    "emit_input_update": ("research_system.cli.interactions.chat_interaction_output_support", "emit_input_update"),
    "emit_meta_update": ("research_system.cli.interactions.chat_interaction_output_support", "emit_meta_update"),
    "emit_warning_update": ("research_system.cli.interactions.chat_interaction_output_support", "emit_warning_update"),
    "request_stop": ("research_system.cli.interactions.chat_interaction_run_support", "request_stop"),
    "submit_tui_input": ("research_system.cli.interactions.chat_interaction_run_support", "submit_tui_input"),
    "toggle_commands_popup": ("research_system.cli.interactions.chat_interaction_run_support", "toggle_commands_popup"),
    "write_tui_update": ("research_system.cli.interactions.chat_interaction_output_support", "write_tui_update"),
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
