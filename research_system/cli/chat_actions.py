from __future__ import annotations

from research_system.cli.chat_action_runtime import build_chat_action_runtime
from research_system.cli.chat_cli_actions import handle_chat_cli_action as runtime_handle_chat_cli_action
from research_system.cli.chat_meta_actions import handle_chat_meta_action as runtime_handle_chat_meta_action


def handle_chat_meta_action(
    *,
    action: str | None,
    parsed: dict,
    state,
    settings,
    emit_text,
    cli_command_runner=None,
    payload_printer=None,
    output_writer=None,
    stop_callback=None,
    print_chat_help_fn=None,
) -> str | None:
    runtime = build_chat_action_runtime(
        action=action,
        parsed=parsed,
        state=state,
        settings=settings,
        emit_text=emit_text,
        cli_command_runner=cli_command_runner,
        payload_printer=payload_printer,
        output_writer=output_writer,
        stop_callback=stop_callback,
        print_chat_help_fn=print_chat_help_fn,
    )
    return runtime_handle_chat_meta_action(runtime)


def handle_chat_cli_action(
    *,
    action: str | None,
    parsed: dict,
    state,
    settings,
    emit_text,
    cli_command_runner,
    payload_printer,
    output_writer=None,
) -> bool:
    runtime = build_chat_action_runtime(
        action=action,
        parsed=parsed,
        state=state,
        settings=settings,
        emit_text=emit_text,
        cli_command_runner=cli_command_runner,
        payload_printer=payload_printer,
        output_writer=output_writer,
    )
    return runtime_handle_chat_cli_action(runtime)
