from __future__ import annotations

from dataclasses import dataclass

from research_system.cli.flow.chat_dispatch import print_streamed_basic_chat_answer
from research_system.cli.memory.chat_memory import remember_basic_chat_turn
from research_system.cli.flow.chat_research_runner import run_chat_research
from research_system.cli.flow.chat_result import print_chat_result_payload
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
    basic_answer_stream = _build_basic_answer_stream(
        settings=settings,
        state=state,
        emit_update=emit_update,
        basic_answer_stream_fn=basic_answer_stream_fn,
    )
    return ChatQueryFlowRuntime(
        basic_answer_fn=lambda **kwargs: basic_answer_stream(
            conversation_context=kwargs.pop("conversation_context", None),
            **kwargs,
        ),
        run_research_fn=run_chat_research_fn or run_chat_research,
        print_result_fn=_build_result_printer(
            settings=settings,
            state=state,
            payload_printer=payload_printer,
            print_chat_result_payload_fn=print_chat_result_payload_fn,
        ),
        remember_basic_chat_turn_fn=lambda **kwargs: remember_basic_chat_turn(
            settings=settings,
            state=state,
            **kwargs,
        ),
    )


def require_chat_cli_runtime(*, cli_command_runner, payload_printer) -> None:
    if cli_command_runner is None:
        raise RuntimeError("cli_command_runner is required for chat command execution")
    if payload_printer is None:
        raise RuntimeError("payload_printer is required for chat command execution")


def _build_basic_answer_stream(*, settings: Settings, state, emit_update, basic_answer_stream_fn=None):
    if basic_answer_stream_fn is not None:
        return basic_answer_stream_fn
    return lambda **kwargs: print_streamed_basic_chat_answer(
        settings=settings,
        state=state,
        emit_text=emit_update,
        **kwargs,
    )


def _build_result_printer(*, settings: Settings, state, payload_printer, print_chat_result_payload_fn=None):
    if print_chat_result_payload_fn is not None:
        return print_chat_result_payload_fn

    def _print_result(payload, *, llm_provider, query, requested_intent, output_writer=None):
        return print_chat_result_payload(
            payload,
            settings=settings,
            state=state,
            llm_provider=llm_provider,
            query=query,
            requested_intent=requested_intent,
            payload_printer=payload_printer,
            output_writer=output_writer,
        )

    return _print_result
