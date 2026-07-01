from __future__ import annotations

from research_system.cli.chat_actions import handle_chat_cli_action, handle_chat_meta_action
from research_system.cli.chat_commands import parse_chat_input
from research_system.cli.chat_memory import (
    ensure_session_thread,
    remember_basic_chat_turn,
)
from research_system.cli.chat_dispatch import (
    decide_chat_dispatch,
    print_streamed_basic_chat_answer,
)
from research_system.cli.chat_command_catalog import print_chat_help
from research_system.cli.chat_research_runner import run_chat_research
from research_system.cli.chat_run_flow import run_chat_query_flow
from research_system.cli.chat_result import (
    print_chat_result_payload,
)
from research_system.cli.chat_updates import dispatch_transcript_update, warning_update
from research_system.runtime.provider_registry import OpenAIProvider
from research_system.runtime.settings import Settings
from research_system.services.task_router import TaskRouter


def _build_query_flow_runtime(
    *,
    settings: Settings,
    state,
    emit_update,
    payload_printer,
    basic_answer_stream_fn=None,
    run_chat_research_fn=None,
    print_chat_result_payload_fn=None,
):
    basic_answer_stream = _build_basic_answer_stream(
        emit_update=emit_update,
        basic_answer_stream_fn=basic_answer_stream_fn,
    )
    return {
        "basic_answer_fn": lambda **kwargs: basic_answer_stream(
            conversation_context=kwargs.pop("conversation_context", None),
            **kwargs,
        ),
        "run_research_fn": run_chat_research_fn or run_chat_research,
        "print_result_fn": _build_result_printer(
            payload_printer=payload_printer,
            print_chat_result_payload_fn=print_chat_result_payload_fn,
        ),
        "remember_basic_chat_turn_fn": lambda **kwargs: remember_basic_chat_turn(
            settings=settings,
            state=state,
            **kwargs,
        ),
    }


def _build_basic_answer_stream(*, emit_update, basic_answer_stream_fn=None):
    if basic_answer_stream_fn is not None:
        return basic_answer_stream_fn
    return lambda **kwargs: print_streamed_basic_chat_answer(emit_text=emit_update, **kwargs)


def _build_result_printer(*, payload_printer, print_chat_result_payload_fn=None):
    if print_chat_result_payload_fn is not None:
        return print_chat_result_payload_fn

    def _print_result(payload, *, llm_provider, query, requested_intent, output_writer=None):
        return print_chat_result_payload(
            payload,
            llm_provider=llm_provider,
            query=query,
            requested_intent=requested_intent,
            payload_printer=payload_printer,
            output_writer=output_writer,
        )

    return _print_result


def _require_chat_cli_runtime(*, cli_command_runner, payload_printer) -> None:
    if cli_command_runner is None:
        raise RuntimeError("cli_command_runner is required for chat command execution")
    if payload_printer is None:
        raise RuntimeError("payload_printer is required for chat command execution")


def handle_chat_line(
    settings: Settings,
    state,
    line: str,
    *,
    llm_provider: OpenAIProvider,
    task_router: TaskRouter,
    cli_command_runner=None,
    payload_printer=None,
    output_writer=None,
    stop_callback=None,
    chat_dispatch_fn=None,
    basic_answer_stream_fn=None,
    run_chat_research_fn=None,
    print_chat_result_payload_fn=None,
) -> str | None:
    emit_update = dispatch_transcript_update
    dispatch_fn = chat_dispatch_fn or decide_chat_dispatch
    ensure_session_thread(settings=settings, state=state)
    flow_runtime = _build_query_flow_runtime(
        settings=settings,
        state=state,
        emit_update=emit_update,
        payload_printer=payload_printer,
        basic_answer_stream_fn=basic_answer_stream_fn,
        run_chat_research_fn=run_chat_research_fn,
        print_chat_result_payload_fn=print_chat_result_payload_fn,
    )

    parsed = parse_chat_input(line)
    action = parsed.get("action")

    outcome = handle_chat_meta_action(
        action=action,
        parsed=parsed,
        state=state,
        settings=settings,
        emit_text=emit_update,
        cli_command_runner=cli_command_runner,
        payload_printer=payload_printer,
        output_writer=output_writer,
        stop_callback=stop_callback,
        print_chat_help_fn=print_chat_help,
    )
    if outcome != "continue":
        return outcome

    _require_chat_cli_runtime(
        cli_command_runner=cli_command_runner,
        payload_printer=payload_printer,
    )

    if handle_chat_cli_action(
        action=action,
        parsed=parsed,
        state=state,
        settings=settings,
        emit_text=emit_update,
        cli_command_runner=cli_command_runner,
        payload_printer=payload_printer,
        output_writer=output_writer,
    ):
        return None

    if run_chat_query_flow(
        parsed=parsed,
        state=state,
        settings=settings,
        llm_provider=llm_provider,
        task_router=task_router,
        emit_text=emit_update,
        dispatch_fn=dispatch_fn,
        conversation_context=None,
        basic_answer_fn=flow_runtime["basic_answer_fn"],
        run_research_fn=flow_runtime["run_research_fn"],
        print_result_fn=flow_runtime["print_result_fn"],
        remember_basic_chat_turn_fn=flow_runtime["remember_basic_chat_turn_fn"],
        output_writer=output_writer,
    ):
        return None

    dispatch_transcript_update(output_writer, warning_update("Unsupported command."))
    return None
