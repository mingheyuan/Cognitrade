from __future__ import annotations

from dataclasses import dataclass

from research_system.cli.chat_action_runtime import build_chat_action_runtime
from research_system.cli.chat_actions import handle_chat_cli_action, handle_chat_meta_action
from research_system.cli.chat_commands import parse_chat_input
from research_system.cli.chat_flow_runtime import build_chat_query_flow_runtime, require_chat_cli_runtime
from research_system.cli.chat_run_flow import run_chat_query_flow
from research_system.cli.chat_updates import dispatch_transcript_update, warning_update
from research_system.runtime.settings import Settings


@dataclass(frozen=True)
class ChatLineDispatcherRuntime:
    settings: Settings
    state: object
    llm_provider: object
    task_router: object
    cli_command_runner: object = None
    payload_printer: object = None
    output_writer: object = None
    stop_callback: object = None
    dispatch_fn: object = None
    basic_answer_stream_fn: object = None
    run_chat_research_fn: object = None
    print_chat_result_payload_fn: object = None
    print_chat_help_fn: object = None
    emit_update: object = dispatch_transcript_update

    def parse(self, line: str) -> dict:
        return parse_chat_input(line)

    def build_action_runtime(self, *, action: str | None, parsed: dict):
        return build_chat_action_runtime(
            action=action,
            parsed=parsed,
            state=self.state,
            settings=self.settings,
            emit_text=self.emit_update,
            cli_command_runner=self.cli_command_runner,
            payload_printer=self.payload_printer,
            output_writer=self.output_writer,
            stop_callback=self.stop_callback,
            print_chat_help_fn=self.print_chat_help_fn,
        )

    def build_flow_runtime(self):
        return build_chat_query_flow_runtime(
            settings=self.settings,
            state=self.state,
            emit_update=self.emit_update,
            payload_printer=self.payload_printer,
            basic_answer_stream_fn=self.basic_answer_stream_fn,
            run_chat_research_fn=self.run_chat_research_fn,
            print_chat_result_payload_fn=self.print_chat_result_payload_fn,
        )

    def emit_unsupported(self) -> None:
        self.emit_update(self.output_writer, warning_update("Unsupported command."))


def build_chat_line_dispatcher_runtime(
    *,
    settings: Settings,
    state,
    llm_provider,
    task_router,
    cli_command_runner=None,
    payload_printer=None,
    output_writer=None,
    stop_callback=None,
    dispatch_fn=None,
    basic_answer_stream_fn=None,
    run_chat_research_fn=None,
    print_chat_result_payload_fn=None,
    print_chat_help_fn=None,
    emit_update=dispatch_transcript_update,
) -> ChatLineDispatcherRuntime:
    return ChatLineDispatcherRuntime(
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
        print_chat_help_fn=print_chat_help_fn,
        emit_update=emit_update,
    )


def dispatch_chat_line(runtime: ChatLineDispatcherRuntime, line: str) -> str | None:
    parsed = runtime.parse(line)
    action = parsed.get("action")

    outcome = handle_chat_meta_action(
        action=action,
        parsed=parsed,
        state=runtime.state,
        settings=runtime.settings,
        emit_text=runtime.emit_update,
        cli_command_runner=runtime.cli_command_runner,
        payload_printer=runtime.payload_printer,
        output_writer=runtime.output_writer,
        stop_callback=runtime.stop_callback,
        print_chat_help_fn=runtime.print_chat_help_fn,
    )
    if outcome != "continue":
        return outcome

    require_chat_cli_runtime(
        cli_command_runner=runtime.cli_command_runner,
        payload_printer=runtime.payload_printer,
    )

    if handle_chat_cli_action(
        action=action,
        parsed=parsed,
        state=runtime.state,
        settings=runtime.settings,
        emit_text=runtime.emit_update,
        cli_command_runner=runtime.cli_command_runner,
        payload_printer=runtime.payload_printer,
        output_writer=runtime.output_writer,
    ):
        return None

    flow_runtime = runtime.build_flow_runtime()
    if run_chat_query_flow(
        parsed=parsed,
        state=runtime.state,
        settings=runtime.settings,
        llm_provider=runtime.llm_provider,
        task_router=runtime.task_router,
        emit_text=runtime.emit_update,
        dispatch_fn=runtime.dispatch_fn,
        conversation_context=None,
        runtime=flow_runtime,
        output_writer=runtime.output_writer,
    ):
        return None

    runtime.emit_unsupported()
    return None
