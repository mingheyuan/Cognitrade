from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit.document import Document

from research_system.cli.memory.chat_memory import (
    archive_session_thread,
    fork_session_thread,
    list_chat_threads,
    resume_session_thread,
    unarchive_session_thread,
)
from research_system.cli.inspect.chat_inspect_keybindings import build_inspect_panel_runtime
from research_system.cli.keybindings.chat_thread_keybindings import build_thread_panel_runtime
from research_system.cli.state.chat_types import TuiSession


def move_output_cursor(session: TuiSession, *, lines: int | None = None, absolute: str | None = None) -> None:
    output_area = session.output_area
    if output_area is None:
        return
    buffer = getattr(output_area, "buffer", None)
    if buffer is None:
        return
    text = getattr(output_area, "text", "")
    if absolute == "start":
        buffer.cursor_position = 0
    elif absolute == "end":
        buffer.cursor_position = len(text)
    elif lines is not None:
        if lines < 0:
            buffer.cursor_up(count=abs(lines))
        elif lines > 0:
            buffer.cursor_down(count=lines)
    if session.app is not None:
        session.app.invalidate()


def set_output_mode(session: TuiSession, mode: str) -> None:
    session.output_mode = mode
    session.follow_output = mode == "live"


@dataclass(frozen=True)
class ChatKeybindingRuntime:
    session: TuiSession
    handle_chat_line: object
    execute_cli_command: object
    submit_tui_input: object
    request_stop: object
    toggle_commands_popup: object
    emit_warning_update: object
    thread_runtime: object
    inspect_runtime: object

    def clear_input(self) -> None:
        if self.session.input_area is not None:
            self.session.input_area.buffer.document = Document(text="")


def build_chat_keybinding_runtime(
    session: TuiSession,
    *,
    handle_chat_line,
    execute_cli_command,
    submit_tui_input,
    request_stop,
    toggle_commands_popup,
    emit_warning_update,
    list_chat_threads_fn=list_chat_threads,
    resume_session_thread_fn=resume_session_thread,
    fork_session_thread_fn=fork_session_thread,
    archive_session_thread_fn=archive_session_thread,
    unarchive_session_thread_fn=unarchive_session_thread,
) -> ChatKeybindingRuntime:
    thread_runtime = build_thread_panel_runtime(
        session=session,
        emit_warning_update=emit_warning_update,
        move_output_cursor=move_output_cursor,
        list_threads=lambda **kwargs: list_chat_threads_fn(**kwargs),
        resume_thread=lambda **kwargs: resume_session_thread_fn(**kwargs),
        fork_thread=lambda **kwargs: fork_session_thread_fn(**kwargs),
        archive_thread=lambda **kwargs: archive_session_thread_fn(**kwargs),
        unarchive_thread=lambda **kwargs: unarchive_session_thread_fn(**kwargs),
    )

    inspect_runtime = build_inspect_panel_runtime(
        session=session,
        execute_cli_command=execute_cli_command,
        emit_warning_update=emit_warning_update,
        move_output_cursor=move_output_cursor,
    )

    return ChatKeybindingRuntime(
        session=session,
        handle_chat_line=handle_chat_line,
        execute_cli_command=execute_cli_command,
        submit_tui_input=submit_tui_input,
        request_stop=request_stop,
        toggle_commands_popup=toggle_commands_popup,
        emit_warning_update=emit_warning_update,
        thread_runtime=thread_runtime,
        inspect_runtime=inspect_runtime,
    )
