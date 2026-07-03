from __future__ import annotations

from research_system.cli.chat_command_catalog import print_chat_help
from research_system.cli.chat_dispatch import decide_chat_dispatch
from research_system.cli.chat_line_dispatcher import build_chat_line_dispatcher_runtime, dispatch_chat_line
from research_system.cli.chat_memory import ensure_session_thread
from research_system.cli.chat_updates import dispatch_transcript_update
from research_system.runtime.provider_registry import OpenAIProvider
from research_system.runtime.settings import Settings
from research_system.services.task_router import TaskRouter


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

    runtime = build_chat_line_dispatcher_runtime(
        settings=settings,
        state=state,
        llm_provider=llm_provider,
        task_router=task_router,
        cli_command_runner=cli_command_runner,
        payload_printer=payload_printer,
        output_writer=output_writer,
        stop_callback=stop_callback,
        dispatch_fn=dispatch_fn,
        basic_answer_stream_fn=basic_answer_stream_fn,
        run_chat_research_fn=run_chat_research_fn,
        print_chat_result_payload_fn=print_chat_result_payload_fn,
        print_chat_help_fn=print_chat_help,
        emit_update=emit_update,
    )
    return dispatch_chat_line(runtime, line)
