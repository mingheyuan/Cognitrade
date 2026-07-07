from __future__ import annotations

from dataclasses import dataclass

from research_system.cli.runtime.chat_runtime_bindings import (
    build_basic_answer_stream_binding,
    build_result_printer_binding,
)
from research_system.runtime.platform.settings import Settings


@dataclass(frozen=True)
class ChatAppRuntime:
    controller_handle_chat_line_fn: object
    cli_command_runner: object
    payload_printer: object
    dispatch_transcript_update_fn: object
    chat_dispatch_fn: object
    basic_answer_stream_print_fn: object
    run_chat_research_fn: object
    print_chat_result_payload_fn: object

    def handle_chat_line(
        self,
        settings: Settings,
        state,
        line: str,
        *,
        llm_provider,
        task_router,
        output_writer=None,
        stop_callback=None,
    ) -> str | None:
        return self.controller_handle_chat_line_fn(
            settings,
            state,
            line,
            llm_provider=llm_provider,
            task_router=task_router,
            cli_command_runner=self.cli_command_runner,
            payload_printer=self.payload_printer,
            output_writer=output_writer,
            stop_callback=stop_callback,
            chat_dispatch_fn=self.chat_dispatch_fn,
            basic_answer_stream_fn=build_basic_answer_stream_binding(
                settings=settings,
                state=state,
                emit_text=self.dispatch_transcript_update_fn,
                basic_answer_stream_fn=self.basic_answer_stream_print_fn,
            ),
            run_chat_research_fn=self.run_chat_research_fn,
            print_chat_result_payload_fn=build_result_printer_binding(
                settings=settings,
                state=state,
                payload_printer=self.payload_printer,
                print_chat_result_payload_fn=self.print_chat_result_payload_fn,
            ),
        )


def build_chat_app_runtime(
    *,
    controller_handle_chat_line_fn,
    cli_command_runner,
    payload_printer,
    dispatch_transcript_update_fn,
    chat_dispatch_fn,
    basic_answer_stream_print_fn,
    run_chat_research_fn,
    print_chat_result_payload_fn,
) -> ChatAppRuntime:
    return ChatAppRuntime(
        controller_handle_chat_line_fn=controller_handle_chat_line_fn,
        cli_command_runner=cli_command_runner,
        payload_printer=payload_printer,
        dispatch_transcript_update_fn=dispatch_transcript_update_fn,
        chat_dispatch_fn=chat_dispatch_fn,
        basic_answer_stream_print_fn=basic_answer_stream_print_fn,
        run_chat_research_fn=run_chat_research_fn,
        print_chat_result_payload_fn=print_chat_result_payload_fn,
    )


def handle_chat_line(
    settings: Settings,
    state,
    line: str,
    *,
    llm_provider,
    task_router,
    runtime: ChatAppRuntime,
    output_writer=None,
    stop_callback=None,
) -> str | None:
    return runtime.handle_chat_line(
        settings,
        state,
        line,
        llm_provider=llm_provider,
        task_router=task_router,
        output_writer=output_writer,
        stop_callback=stop_callback,
    )


def print_cli_payload(
    payload: object,
    *,
    as_json: bool,
    print_payload_fn,
    dispatch_transcript_update_fn,
    output_writer=None,
) -> int:
    return print_payload_fn(
        payload,
        as_json=as_json,
        emit_text=dispatch_transcript_update_fn,
        output_writer=output_writer,
    )
