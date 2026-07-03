from __future__ import annotations

from importlib import import_module

__all__ = [
    "CHAT_COMMANDS",
    "CHAT_COMMAND_SUMMARY_LINES",
    "CHAT_HELP_LINES",
    "PROPOSAL_COMMANDS",
    "RUN_ID_COMMANDS",
    "STATUS_FILTER_COMMANDS",
    "SUBAGENT_COMMANDS",
    "ChatCommandCompleter",
    "build_cli_command_payload",
    "build_research_request",
    "clear_pending_clarification",
    "execute_cli_payload_command",
    "parse_chat_input",
    "print_chat_help",
]

_EXPORTS = {
    "CHAT_COMMANDS": ("research_system.cli.commands.chat_command_completion_support", "CHAT_COMMANDS"),
    "CHAT_COMMAND_SUMMARY_LINES": ("research_system.cli.commands.chat_command_catalog", "CHAT_COMMAND_SUMMARY_LINES"),
    "CHAT_HELP_LINES": ("research_system.cli.commands.chat_command_catalog", "CHAT_HELP_LINES"),
    "PROPOSAL_COMMANDS": ("research_system.cli.commands.cli_command_payloads", "PROPOSAL_COMMANDS"),
    "RUN_ID_COMMANDS": ("research_system.cli.commands.cli_command_payloads", "RUN_ID_COMMANDS"),
    "STATUS_FILTER_COMMANDS": ("research_system.cli.commands.cli_command_payloads", "STATUS_FILTER_COMMANDS"),
    "SUBAGENT_COMMANDS": ("research_system.cli.commands.cli_command_payloads", "SUBAGENT_COMMANDS"),
    "ChatCommandCompleter": ("research_system.cli.commands.chat_command_completion_support", "ChatCommandCompleter"),
    "build_cli_command_payload": ("research_system.cli.commands.cli_command_payloads", "build_cli_command_payload"),
    "build_research_request": ("research_system.cli.commands.research_request_payloads", "build_research_request"),
    "clear_pending_clarification": (
        "research_system.cli.commands.chat_pending_clarification_support",
        "clear_pending_clarification",
    ),
    "execute_cli_payload_command": ("research_system.cli.commands.cli_command_dispatch", "execute_cli_payload_command"),
    "parse_chat_input": ("research_system.cli.commands.chat_command_parse_support", "parse_chat_input"),
    "print_chat_help": ("research_system.cli.commands.chat_command_catalog", "print_chat_help"),
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
