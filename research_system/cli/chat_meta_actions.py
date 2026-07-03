from __future__ import annotations

from research_system.cli.chat_action_runtime import ChatActionRuntime
from research_system.cli.chat_meta_display_actions import (
    handle_show_last_action as display_handle_show_last_action,
    handle_show_thread_action as display_handle_show_thread_action,
)
from research_system.cli.chat_meta_state_actions import (
    handle_clear_action as state_handle_clear_action,
    handle_set_events_action as state_handle_set_events_action,
    handle_set_intent_action as state_handle_set_intent_action,
    handle_set_run_id_action as state_handle_set_run_id_action,
    handle_set_thinking_action as state_handle_set_thinking_action,
)
from research_system.cli.chat_meta_thread_actions import (
    handle_archive_thread_action as thread_handle_archive_thread_action,
    handle_compact_thread_action as thread_handle_compact_thread_action,
    handle_fork_thread_action as thread_handle_fork_thread_action,
    handle_new_thread_action as thread_handle_new_thread_action,
    handle_read_thread_action as thread_handle_read_thread_action,
    handle_rename_thread_action as thread_handle_rename_thread_action,
    handle_set_thread_action as thread_handle_set_thread_action,
    handle_unarchive_thread_action as thread_handle_unarchive_thread_action,
)


def _handle_noop_action(runtime: ChatActionRuntime) -> None:
    return None


def _handle_error_action(runtime: ChatActionRuntime) -> None:
    runtime.emit_meta(str(runtime.parsed.get("message") or "Invalid command."))
    return None


def _handle_commands_action(runtime: ChatActionRuntime) -> str | None:
    if runtime.output_writer is not None:
        runtime.emit_meta("commands popup toggled")
        return "commands_popup"
    if runtime.print_chat_help_fn is not None:
        runtime.print_chat_help_fn(output_writer=runtime.output_writer)
    return None


def _handle_help_action(runtime: ChatActionRuntime) -> None:
    if runtime.print_chat_help_fn is not None:
        runtime.print_chat_help_fn(output_writer=runtime.output_writer)
    return None


def _handle_new_thread_action(runtime: ChatActionRuntime) -> None:
    return thread_handle_new_thread_action(runtime)


def _handle_show_thread_action(runtime: ChatActionRuntime) -> None:
    return display_handle_show_thread_action(runtime)


def _handle_rename_thread_action(runtime: ChatActionRuntime) -> None:
    return thread_handle_rename_thread_action(runtime)


def _handle_read_thread_action(runtime: ChatActionRuntime) -> None:
    return thread_handle_read_thread_action(runtime)


def _handle_compact_thread_action(runtime: ChatActionRuntime) -> None:
    return thread_handle_compact_thread_action(runtime)


def _handle_fork_thread_action(runtime: ChatActionRuntime) -> None:
    return thread_handle_fork_thread_action(runtime)


def _handle_archive_thread_action(runtime: ChatActionRuntime) -> None:
    return thread_handle_archive_thread_action(runtime)


def _handle_unarchive_thread_action(runtime: ChatActionRuntime) -> None:
    return thread_handle_unarchive_thread_action(runtime)


def _handle_exit_action(runtime: ChatActionRuntime) -> str:
    return "exit"


def _handle_set_intent_action(runtime: ChatActionRuntime) -> None:
    return state_handle_set_intent_action(runtime)


def _handle_set_run_id_action(runtime: ChatActionRuntime) -> None:
    return state_handle_set_run_id_action(runtime)


def _handle_clear_action(runtime: ChatActionRuntime) -> None:
    return state_handle_clear_action(runtime)


def _handle_set_thinking_action(runtime: ChatActionRuntime) -> None:
    return state_handle_set_thinking_action(runtime)


def _handle_set_events_action(runtime: ChatActionRuntime) -> None:
    return state_handle_set_events_action(runtime)


def _handle_set_thread_action(runtime: ChatActionRuntime) -> None:
    return thread_handle_set_thread_action(runtime)


def _handle_stop_action(runtime: ChatActionRuntime) -> None:
    if runtime.stop_callback is not None:
        runtime.stop_callback()
        runtime.emit_meta("stop requested")
        return None
    resolved_run_id = runtime.state.last_run_id
    if not resolved_run_id:
        runtime.emit_warning("no active or recent run to stop")
        return None
    payload = runtime.run_cli_command(command="cancel", run_id=resolved_run_id)
    runtime.print_payload(payload)
    return None


def _handle_show_last_action(runtime: ChatActionRuntime) -> None:
    return display_handle_show_last_action(runtime)


_META_ACTION_HANDLERS = {
    "noop": _handle_noop_action,
    "error": _handle_error_action,
    "commands": _handle_commands_action,
    "help": _handle_help_action,
    "new_thread": _handle_new_thread_action,
    "fork_thread": _handle_fork_thread_action,
    "archive_thread": _handle_archive_thread_action,
    "unarchive_thread": _handle_unarchive_thread_action,
    "show_thread": _handle_show_thread_action,
    "rename_thread": _handle_rename_thread_action,
    "read_thread": _handle_read_thread_action,
    "compact_thread": _handle_compact_thread_action,
    "exit": _handle_exit_action,
    "set_intent": _handle_set_intent_action,
    "set_run_id": _handle_set_run_id_action,
    "clear": _handle_clear_action,
    "set_thinking": _handle_set_thinking_action,
    "set_events": _handle_set_events_action,
    "set_thread": _handle_set_thread_action,
    "stop": _handle_stop_action,
    "show_last": _handle_show_last_action,
}


def handle_chat_meta_action(runtime: ChatActionRuntime) -> str | None:
    handler = _META_ACTION_HANDLERS.get(runtime.action or "")
    if handler is None:
        return "continue"
    return handler(runtime)
