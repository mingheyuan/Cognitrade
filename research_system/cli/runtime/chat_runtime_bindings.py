from __future__ import annotations

from research_system.cli.flow.chat_dispatch import print_streamed_basic_chat_answer
from research_system.cli.flow.chat_result import print_chat_result_payload
from research_system.cli.memory.chat_memory import remember_basic_chat_turn
from research_system.runtime.platform.settings import Settings


def build_basic_answer_stream_binding(
    *,
    settings: Settings,
    state,
    emit_text=None,
    emit_update=None,
    basic_answer_stream_fn=None,
):
    target = basic_answer_stream_fn or print_streamed_basic_chat_answer
    resolved_emit = emit_text if emit_text is not None else emit_update

    def _run(**kwargs):
        return target(
            settings=settings,
            state=state,
            emit_text=resolved_emit,
            **kwargs,
        )

    return _run


def build_query_flow_basic_answer_binding(*, basic_answer_stream):
    def _run(**kwargs):
        return basic_answer_stream(
            conversation_context=kwargs.pop("conversation_context", None),
            **kwargs,
        )

    return _run


def build_result_printer_binding(
    *,
    settings: Settings,
    state,
    payload_printer,
    print_chat_result_payload_fn=None,
):
    target = print_chat_result_payload_fn or print_chat_result_payload

    def _run(payload, *, llm_provider, query, requested_intent, output_writer=None):
        return target(
            payload,
            settings=settings,
            state=state,
            llm_provider=llm_provider,
            query=query,
            requested_intent=requested_intent,
            payload_printer=payload_printer,
            output_writer=output_writer,
        )

    return _run


def build_basic_chat_turn_rememberer(*, settings: Settings, state, remember_basic_chat_turn_fn=None):
    target = remember_basic_chat_turn_fn or remember_basic_chat_turn

    def _run(**kwargs):
        return target(
            settings=settings,
            state=state,
            **kwargs,
        )

    return _run


__all__ = [
    "build_basic_answer_stream_binding",
    "build_basic_chat_turn_rememberer",
    "build_query_flow_basic_answer_binding",
    "build_result_printer_binding",
]
