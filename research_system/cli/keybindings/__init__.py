from __future__ import annotations

from importlib import import_module

__all__ = [
    "ChatKeybindingRuntime",
    "ThreadPanelRuntime",
    "build_chat_keybinding_runtime",
    "build_chat_keybindings",
    "build_chat_keybindings_from_runtime",
    "build_thread_panel_runtime",
    "move_output_cursor",
    "set_output_mode",
]

_EXPORTS = {
    "ChatKeybindingRuntime": ("research_system.cli.keybindings.chat_keybinding_runtime", "ChatKeybindingRuntime"),
    "ThreadPanelRuntime": ("research_system.cli.keybindings.chat_thread_state", "ThreadPanelRuntime"),
    "build_chat_keybinding_runtime": (
        "research_system.cli.keybindings.chat_keybinding_runtime",
        "build_chat_keybinding_runtime",
    ),
    "build_chat_keybindings": ("research_system.cli.keybindings.chat_keybindings", "build_chat_keybindings"),
    "build_chat_keybindings_from_runtime": (
        "research_system.cli.keybindings.chat_keybinding_bindings",
        "build_chat_keybindings_from_runtime",
    ),
    "build_thread_panel_runtime": ("research_system.cli.keybindings.chat_thread_state", "build_thread_panel_runtime"),
    "move_output_cursor": ("research_system.cli.keybindings.chat_keybinding_runtime", "move_output_cursor"),
    "set_output_mode": ("research_system.cli.keybindings.chat_keybinding_runtime", "set_output_mode"),
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
