from __future__ import annotations

from research_system.cli.chat_memory import (
    archive_session_thread,
    fork_session_thread,
    list_chat_threads,
    resume_session_thread,
    unarchive_session_thread,
)
from research_system.cli.chat_inspect_keybindings import load_events_panel
from research_system.cli.chat_keybinding_bindings import build_chat_keybindings_from_runtime
from research_system.cli.chat_keybinding_runtime import build_chat_keybinding_runtime
from research_system.cli.chat_types import TuiSession


def build_chat_keybindings(
    session: TuiSession,
    *,
    handle_chat_line,
    execute_cli_command,
    submit_tui_input,
    request_stop,
    toggle_commands_popup,
    emit_warning_update,
):
    runtime = build_chat_keybinding_runtime(
        session,
        handle_chat_line=handle_chat_line,
        execute_cli_command=execute_cli_command,
        submit_tui_input=submit_tui_input,
        request_stop=request_stop,
        toggle_commands_popup=toggle_commands_popup,
        emit_warning_update=emit_warning_update,
        list_chat_threads_fn=lambda **kwargs: list_chat_threads(**kwargs),
        resume_session_thread_fn=lambda **kwargs: resume_session_thread(**kwargs),
        fork_session_thread_fn=lambda **kwargs: fork_session_thread(**kwargs),
        archive_session_thread_fn=lambda **kwargs: archive_session_thread(**kwargs),
        unarchive_session_thread_fn=lambda **kwargs: unarchive_session_thread(**kwargs),
    )
    return build_chat_keybindings_from_runtime(runtime)
