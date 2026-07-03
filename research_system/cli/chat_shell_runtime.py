from __future__ import annotations

from dataclasses import dataclass

from research_system.cli.chat_interactions import emit_meta_update
from research_system.cli.chat_types import TuiSession
from research_system.cli.chat_ui_runtime_core import ChatUiRuntime, build_chat_ui_runtime


@dataclass(frozen=True)
class ChatShellRuntime:
    session: TuiSession
    ui_runtime: ChatUiRuntime
    banner_lines: tuple[str, ...]

    def emit_banner(self) -> None:
        for line in self.banner_lines:
            emit_meta_update(self.session, line)


def build_chat_shell_runtime(
    *,
    session: TuiSession,
    handle_chat_line,
    execute_cli_command,
    banner_lines,
) -> ChatShellRuntime:
    return ChatShellRuntime(
        session=session,
        ui_runtime=build_chat_ui_runtime(
            handle_chat_line=handle_chat_line,
            execute_cli_command=execute_cli_command,
        ),
        banner_lines=tuple(banner_lines),
    )


__all__ = ["ChatShellRuntime", "build_chat_shell_runtime"]
