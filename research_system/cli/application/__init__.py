from __future__ import annotations

from importlib import import_module

__all__ = [
    "build_app_parser",
    "build_app_settings",
    "build_parser",
    "execute_app_main_command",
    "execute_cli_command",
    "handle_chat_line",
    "main",
    "print_cli_payload",
]

_EXPORTS = {
    "build_app_parser": ("research_system.cli.application.app_parser_support", "build_app_parser"),
    "build_app_settings": ("research_system.cli.application.app_entry_support", "build_app_settings"),
    "build_parser": ("research_system.cli.application.app_entrypoint", "build_parser"),
    "execute_app_main_command": ("research_system.cli.application.app_entry_support", "execute_app_main_command"),
    "execute_cli_command": ("research_system.cli.application.app_entrypoint", "execute_cli_command"),
    "handle_chat_line": ("research_system.cli.application.app_entrypoint", "handle_chat_line"),
    "main": ("research_system.cli.application.app_entrypoint", "main"),
    "print_cli_payload": ("research_system.cli.application.app_entrypoint", "print_cli_payload"),
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
