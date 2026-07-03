from __future__ import annotations

from dataclasses import dataclass

from research_system.cli.state.chat_updates import meta_update, warning_update
from research_system.runtime.platform.settings import Settings


@dataclass(frozen=True)
class ChatActionRuntime:
    action: str | None
    parsed: dict
    state: object
    settings: Settings
    emit_text: object
    output_writer: object = None
    cli_command_runner: object = None
    payload_printer: object = None
    stop_callback: object = None
    print_chat_help_fn: object = None

    def emit(self, update) -> None:
        self.emit_text(self.output_writer, update)

    def emit_meta(self, text: str, *, session_patch=None, state_patch=None) -> None:
        self.emit(
            meta_update(
                text,
                session_patch=session_patch,
                state_patch=state_patch,
            )
        )

    def emit_warning(self, text: str) -> None:
        self.emit(warning_update(text))

    def run_cli_command(self, **kwargs) -> object:
        if self.cli_command_runner is None:
            raise RuntimeError("cli_command_runner is required for chat command execution")
        return self.cli_command_runner(self.settings, **kwargs)

    def print_payload(self, payload: object) -> None:
        if self.payload_printer is None:
            raise RuntimeError("payload_printer is required for chat command execution")
        self.payload_printer(payload, as_json=False, output_writer=self.output_writer)


def build_chat_action_runtime(
    *,
    action: str | None,
    parsed: dict,
    state,
    settings: Settings,
    emit_text,
    cli_command_runner=None,
    payload_printer=None,
    output_writer=None,
    stop_callback=None,
    print_chat_help_fn=None,
) -> ChatActionRuntime:
    return ChatActionRuntime(
        action=action,
        parsed=parsed,
        state=state,
        settings=settings,
        emit_text=emit_text,
        output_writer=output_writer,
        cli_command_runner=cli_command_runner,
        payload_printer=payload_printer,
        stop_callback=stop_callback,
        print_chat_help_fn=print_chat_help_fn,
    )
