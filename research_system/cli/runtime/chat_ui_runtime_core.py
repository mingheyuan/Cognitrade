from __future__ import annotations

from dataclasses import dataclass

from research_system.cli.interactions.chat_interactions import (
    emit_warning_update,
    request_stop,
    submit_tui_input,
    toggle_commands_popup,
)
from research_system.cli.state.chat_types import TuiSession


@dataclass(frozen=True)
class ChatUiRuntime:
    handle_chat_line: object
    execute_cli_command: object
    submit_tui_input_fn: object
    request_stop_fn: object
    toggle_commands_popup_fn: object
    emit_warning_update_fn: object

    async def submit_tui_input(self, session: TuiSession) -> None:
        await self.submit_tui_input_fn(
            session,
            handle_chat_line=self.handle_chat_line,
            execute_cli_command=self.execute_cli_command,
        )

    def request_stop(self, session: TuiSession) -> None:
        self.request_stop_fn(session, execute_cli_command=self.execute_cli_command)

    def toggle_commands_popup(self, session: TuiSession, *, visible: bool | None = None) -> None:
        self.toggle_commands_popup_fn(session, visible=visible)

    def emit_warning_update(self, session: TuiSession, message: str) -> None:
        self.emit_warning_update_fn(session, message)


def build_chat_ui_runtime(
    *,
    handle_chat_line,
    execute_cli_command,
    submit_tui_input_fn=submit_tui_input,
    request_stop_fn=request_stop,
    toggle_commands_popup_fn=toggle_commands_popup,
    emit_warning_update_fn=emit_warning_update,
) -> ChatUiRuntime:
    return ChatUiRuntime(
        handle_chat_line=handle_chat_line,
        execute_cli_command=execute_cli_command,
        submit_tui_input_fn=submit_tui_input_fn,
        request_stop_fn=request_stop_fn,
        toggle_commands_popup_fn=toggle_commands_popup_fn,
        emit_warning_update_fn=emit_warning_update_fn,
    )


__all__ = ["ChatUiRuntime", "build_chat_ui_runtime"]
