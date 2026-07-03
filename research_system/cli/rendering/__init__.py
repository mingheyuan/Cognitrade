from __future__ import annotations

from importlib import import_module

__all__ = [
    "apply_session_state_patch",
    "apply_shell_state_patch",
    "apply_tui_update",
    "build_tui_hint_text",
    "build_tui_status_text",
    "commands_popup_text",
    "has_shell_state_patch",
    "refresh_output_area",
    "refresh_tui",
    "render_tui_output",
]

_EXPORTS = {
    "apply_session_state_patch": ("research_system.cli.rendering.chat_rendering", "apply_session_state_patch"),
    "apply_shell_state_patch": ("research_system.cli.rendering.chat_rendering", "apply_shell_state_patch"),
    "apply_tui_update": ("research_system.cli.rendering.chat_rendering", "apply_tui_update"),
    "build_tui_hint_text": ("research_system.cli.rendering.chat_rendering", "build_tui_hint_text"),
    "build_tui_status_text": ("research_system.cli.rendering.chat_rendering", "build_tui_status_text"),
    "commands_popup_text": ("research_system.cli.rendering.chat_rendering", "commands_popup_text"),
    "has_shell_state_patch": ("research_system.cli.rendering.chat_rendering", "has_shell_state_patch"),
    "refresh_output_area": ("research_system.cli.rendering.chat_rendering_output_refresh", "refresh_output_area"),
    "refresh_tui": ("research_system.cli.rendering.chat_rendering", "refresh_tui"),
    "render_tui_output": ("research_system.cli.rendering.chat_rendering", "render_tui_output"),
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
