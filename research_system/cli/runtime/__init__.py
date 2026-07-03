from __future__ import annotations

from importlib import import_module

__all__ = [
    "ChatActionRuntime",
    "ChatAppRuntime",
    "ChatEntryRuntime",
    "ChatFallbackRuntime",
    "ChatLineDispatcherRuntime",
    "ChatQueryFlowRuntime",
    "ChatRuntimeContext",
    "ChatShellRuntime",
    "ChatUiRuntime",
    "CliCommandRuntime",
    "build_chat_action_runtime",
    "build_chat_app_runtime",
    "build_chat_entry_runtime",
    "build_chat_fallback_runtime",
    "build_chat_line_dispatcher_runtime",
    "build_chat_query_flow_runtime",
    "build_chat_shell_runtime",
    "build_chat_startup_context",
    "build_chat_ui_runtime",
    "build_cli_command_runtime",
    "dispatch_chat_line",
    "execute_cli_command",
    "handle_chat_cli_action",
    "handle_chat_meta_action",
    "handle_chat_line",
    "print_cli_payload",
    "run_chat_entry",
]

_EXPORTS = {
    "ChatActionRuntime": ("research_system.cli.runtime.chat_action_runtime", "ChatActionRuntime"),
    "ChatAppRuntime": ("research_system.cli.runtime.app.chat_runtime", "ChatAppRuntime"),
    "ChatEntryRuntime": ("research_system.cli.runtime.app.chat_entry_runtime", "ChatEntryRuntime"),
    "ChatFallbackRuntime": ("research_system.cli.runtime.chat_fallback_runtime", "ChatFallbackRuntime"),
    "ChatLineDispatcherRuntime": ("research_system.cli.runtime.chat_line_dispatcher", "ChatLineDispatcherRuntime"),
    "ChatQueryFlowRuntime": ("research_system.cli.runtime.chat_flow_runtime", "ChatQueryFlowRuntime"),
    "ChatRuntimeContext": ("research_system.cli.runtime.chat_session_bootstrap", "ChatRuntimeContext"),
    "ChatShellRuntime": ("research_system.cli.runtime.chat_shell_runtime", "ChatShellRuntime"),
    "ChatUiRuntime": ("research_system.cli.runtime.chat_ui_runtime_core", "ChatUiRuntime"),
    "CliCommandRuntime": ("research_system.cli.runtime.app.cli_runtime", "CliCommandRuntime"),
    "build_chat_action_runtime": ("research_system.cli.runtime.chat_action_runtime", "build_chat_action_runtime"),
    "build_chat_app_runtime": ("research_system.cli.runtime.app.chat_runtime", "build_chat_app_runtime"),
    "build_chat_entry_runtime": ("research_system.cli.runtime.app.chat_entry_runtime", "build_chat_entry_runtime"),
    "build_chat_fallback_runtime": ("research_system.cli.runtime.chat_fallback_runtime", "build_chat_fallback_runtime"),
    "build_chat_line_dispatcher_runtime": (
        "research_system.cli.runtime.chat_line_dispatcher",
        "build_chat_line_dispatcher_runtime",
    ),
    "build_chat_query_flow_runtime": ("research_system.cli.runtime.chat_flow_runtime", "build_chat_query_flow_runtime"),
    "build_chat_shell_runtime": ("research_system.cli.runtime.chat_shell_runtime", "build_chat_shell_runtime"),
    "build_chat_startup_context": ("research_system.cli.runtime.chat_session_bootstrap", "build_chat_startup_context"),
    "build_chat_ui_runtime": ("research_system.cli.runtime.chat_ui_runtime_core", "build_chat_ui_runtime"),
    "build_cli_command_runtime": ("research_system.cli.runtime.app.cli_runtime", "build_cli_command_runtime"),
    "dispatch_chat_line": ("research_system.cli.runtime.chat_line_dispatcher", "dispatch_chat_line"),
    "execute_cli_command": ("research_system.cli.runtime.app.cli_runtime", "execute_cli_command"),
    "handle_chat_cli_action": ("research_system.cli.runtime.chat_actions", "handle_chat_cli_action"),
    "handle_chat_meta_action": ("research_system.cli.runtime.chat_actions", "handle_chat_meta_action"),
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
