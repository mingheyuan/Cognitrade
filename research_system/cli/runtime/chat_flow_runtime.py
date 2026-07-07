from __future__ import annotations

from dataclasses import dataclass

from research_system.cli.flow.chat_research_runner import run_chat_research
from research_system.cli.runtime.chat_runtime_bindings import (
    build_basic_answer_stream_binding,
    build_basic_chat_turn_rememberer,
    build_query_flow_basic_answer_binding,
    build_result_printer_binding,
)
from research_system.runtime.platform.settings import Settings


@dataclass(frozen=True)
class ChatQueryFlowRuntime:
    basic_answer_fn: object
    run_research_fn: object
    print_result_fn: object
    remember_basic_chat_turn_fn: object


def build_chat_query_flow_runtime(
    *,
    settings: Settings,
    state,
    emit_update,
    payload_printer,
    basic_answer_stream_fn=None,
    run_chat_research_fn=None,
    print_chat_result_payload_fn=None,
) -> ChatQueryFlowRuntime:
    basic_answer_stream = build_basic_answer_stream_binding(
        settings=settings,
        state=state,
        emit_text=emit_update,
        basic_answer_stream_fn=basic_answer_stream_fn,
    )
    return ChatQueryFlowRuntime(
        basic_answer_fn=build_query_flow_basic_answer_binding(
            basic_answer_stream=basic_answer_stream,
        ),
        run_research_fn=run_chat_research_fn or run_chat_research,
        print_result_fn=build_result_printer_binding(
            settings=settings,
            state=state,
            payload_printer=payload_printer,
            print_chat_result_payload_fn=print_chat_result_payload_fn,
        ),
        remember_basic_chat_turn_fn=build_basic_chat_turn_rememberer(
            settings=settings,
            state=state,
        ),
    )


def require_chat_cli_runtime(*, cli_command_runner, payload_printer) -> None:
    if cli_command_runner is None:
        raise RuntimeError("cli_command_runner is required for chat command execution")
    if payload_printer is None:
        raise RuntimeError("payload_printer is required for chat command execution")
