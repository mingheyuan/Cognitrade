from __future__ import annotations

from research_system.cli.flow.chat_dispatch import print_streamed_basic_chat_answer
from research_system.cli.flow.chat_result import print_chat_result_payload
from research_system.cli.memory.chat_memory import remember_basic_chat_turn
from research_system.runtime.platform.settings import Settings

_BASIC_ANSWER_STREAM_BINDING = "basic_answer_stream"
_RESULT_PRINTER_BINDING = "result_printer"
_BASIC_CHAT_TURN_REMEMBERER_BINDING = "basic_chat_turn_rememberer"


def _binding_kind(target) -> str | None:
    return getattr(target, "__chat_binding_kind__", None)


def build_basic_answer_stream_binding(
    *,
    settings: Settings,
    state,
    emit_text=None,
    emit_update=None,
    basic_answer_stream_fn=None,
):
    target = basic_answer_stream_fn or print_streamed_basic_chat_answer
    if _binding_kind(target) == _BASIC_ANSWER_STREAM_BINDING:
        return target
    resolved_emit = emit_text if emit_text is not None else emit_update

    def _run(**kwargs):
        kwargs.pop("settings", None)
        kwargs.pop("state", None)
        kwargs.pop("emit_text", None)
        kwargs.pop("emit_update", None)
        return target(
            settings=settings,
            state=state,
            emit_text=resolved_emit,
            **kwargs,
        )

    _run.__chat_binding_kind__ = _BASIC_ANSWER_STREAM_BINDING
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
    if _binding_kind(target) == _RESULT_PRINTER_BINDING:
        return target

    def _run(
        payload,
        *,
        llm_provider=None,
        query=None,
        requested_intent=None,
        output_writer=None,
        **kwargs,
    ):
        kwargs.pop("settings", None)
        kwargs.pop("state", None)
        kwargs.pop("payload_printer", None)
        llm_provider = llm_provider if llm_provider is not None else kwargs.pop("llm_provider", None)
        query = query if query is not None else kwargs.pop("query", None)
        requested_intent = (
            requested_intent
            if requested_intent is not None
            else kwargs.pop("requested_intent", None)
        )
        output_writer = (
            output_writer if output_writer is not None else kwargs.pop("output_writer", None)
        )
        return target(
            payload,
            settings=settings,
            state=state,
            llm_provider=llm_provider,
            query=query,
            requested_intent=requested_intent,
            payload_printer=payload_printer,
            output_writer=output_writer,
            **kwargs,
        )

    _run.__chat_binding_kind__ = _RESULT_PRINTER_BINDING
    return _run


def build_basic_chat_turn_rememberer(*, settings: Settings, state, remember_basic_chat_turn_fn=None):
    target = remember_basic_chat_turn_fn or remember_basic_chat_turn
    if _binding_kind(target) == _BASIC_CHAT_TURN_REMEMBERER_BINDING:
        return target

    def _run(**kwargs):
        kwargs.pop("settings", None)
        kwargs.pop("state", None)
        return target(
            settings=settings,
            state=state,
            **kwargs,
        )

    _run.__chat_binding_kind__ = _BASIC_CHAT_TURN_REMEMBERER_BINDING
    return _run


__all__ = [
    "build_basic_answer_stream_binding",
    "build_basic_chat_turn_rememberer",
    "build_query_flow_basic_answer_binding",
    "build_result_printer_binding",
]
