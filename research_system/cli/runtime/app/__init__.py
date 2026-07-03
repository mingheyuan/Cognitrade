from __future__ import annotations

from importlib import import_module

__all__ = [
    "ChatAppRuntime",
    "ChatEntryRuntime",
    "CliCommandRuntime",
    "build_chat_app_runtime",
    "build_chat_entry_runtime",
    "build_cli_command_runtime",
    "execute_cli_command",
    "handle_chat_line",
    "print_cli_payload",
    "run_chat_entry",
]

_EXPORTS = {
    "ChatAppRuntime": ("research_system.cli.runtime.app.chat_runtime", "ChatAppRuntime"),
    "ChatEntryRuntime": ("research_system.cli.runtime.app.chat_entry_runtime", "ChatEntryRuntime"),
    "CliCommandRuntime": ("research_system.cli.runtime.app.cli_runtime", "CliCommandRuntime"),
    "build_chat_app_runtime": ("research_system.cli.runtime.app.chat_runtime", "build_chat_app_runtime"),
    "build_chat_entry_runtime": ("research_system.cli.runtime.app.chat_entry_runtime", "build_chat_entry_runtime"),
    "build_cli_command_runtime": ("research_system.cli.runtime.app.cli_runtime", "build_cli_command_runtime"),
    "execute_cli_command": ("research_system.cli.runtime.app.cli_runtime", "execute_cli_command"),
    "handle_chat_line": ("research_system.cli.runtime.app.chat_runtime", "handle_chat_line"),
    "print_cli_payload": ("research_system.cli.runtime.app.chat_runtime", "print_cli_payload"),
    "run_chat_entry": ("research_system.cli.runtime.app.chat_entry_runtime", "run_chat_entry"),
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
