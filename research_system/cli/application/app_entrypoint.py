from __future__ import annotations

from research_system.cli.runtime.app import (
    build_chat_app_runtime,
    build_cli_command_runtime,
    execute_cli_command as runtime_execute_cli_command,
    handle_chat_line as runtime_handle_chat_line,
    print_cli_payload as runtime_print_cli_payload,
)
from research_system.cli.application.app_entry_support import build_app_settings, execute_app_main_command
from research_system.cli.application.app_parser_support import build_app_parser
from research_system.cli.flow.chat_controller import handle_chat_line as controller_handle_chat_line
from research_system.cli.flow.chat_dispatch import decide_chat_dispatch, print_streamed_basic_chat_answer
from research_system.cli.flow.chat_result import print_chat_result_payload
from research_system.cli.commands.research_request_payloads import build_research_request
from research_system.cli.runtime.chat_research_runner_impl import run_chat_research
from research_system.cli.state.chat_types import ChatSessionState
from research_system.cli.state.chat_updates import dispatch_transcript_update
from research_system.cli.commands.cli_command_dispatch import execute_cli_payload_command
from research_system.cli.payloads.payload_rendering import print_payload
from research_system.runtime.platform.checks import run_checks
from research_system.runtime.diagnostics.doctoring.doctor import build_status, run_doctor
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.runtime.platform.settings import Settings
from research_system.services.routing.router.task_router import TaskRouter


def build_parser():
    return build_app_parser()


def main(
    argv: list[str] | None = None,
    *,
    run_chat_tui_fn,
    run_chat_fallback_fn,
    execute_cli_command_fn,
    handle_chat_line_fn,
    print_payload_fn,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = build_app_settings()
    return execute_app_main_command(
        settings,
        args,
        run_chat_tui_fn=run_chat_tui_fn,
        run_chat_fallback_fn=run_chat_fallback_fn,
        handle_chat_line_fn=handle_chat_line_fn,
        execute_cli_command_fn=execute_cli_command_fn,
        print_payload_fn=print_payload_fn,
    )


def execute_cli_command(
    settings: Settings,
    *,
    command: str,
    query: str | None = None,
    intent: str | None = None,
    run_id: str | None = None,
    ticket_id: str | None = None,
    limit: int = 20,
    status_filter: str | None = None,
    activated_by: str | None = None,
    delegate_subagent_type: str | None = None,
    candidate_subagent_type: str | None = None,
    registration_type: str | None = None,
    skill_name: str | None = None,
    snapshot_type: str | None = None,
    source_id: str | None = None,
    endpoint_path: str | None = None,
    http_method: str | None = None,
    openapi_spec_uri: str | None = None,
    openapi_operation_id: str | None = None,
    timeout_seconds: float | None = None,
    orchestrator_cls=ResearchOrchestrator,
) -> object:
    runtime = build_cli_command_runtime(
        orchestrator_cls=orchestrator_cls,
        build_research_request_fn=build_research_request,
        execute_cli_payload_command_fn=execute_cli_payload_command,
        run_checks_fn=run_checks,
        build_status_fn=build_status,
        run_doctor_fn=run_doctor,
    )
    return runtime_execute_cli_command(
        settings,
        command=command,
        query=query,
        intent=intent,
        run_id=run_id,
        ticket_id=ticket_id,
        limit=limit,
        status_filter=status_filter,
        activated_by=activated_by,
        delegate_subagent_type=delegate_subagent_type,
        candidate_subagent_type=candidate_subagent_type,
        registration_type=registration_type,
        skill_name=skill_name,
        snapshot_type=snapshot_type,
        source_id=source_id,
        endpoint_path=endpoint_path,
        http_method=http_method,
        openapi_spec_uri=openapi_spec_uri,
        openapi_operation_id=openapi_operation_id,
        timeout_seconds=timeout_seconds,
        runtime=runtime,
    )


def handle_chat_line(
    settings: Settings,
    state: ChatSessionState,
    line: str,
    *,
    llm_provider: OpenAIProvider,
    task_router: TaskRouter,
    output_writer=None,
    stop_callback=None,
    execute_cli_command_fn,
    print_payload_fn,
) -> str | None:
    runtime = build_chat_app_runtime(
        controller_handle_chat_line_fn=controller_handle_chat_line,
        cli_command_runner=execute_cli_command_fn,
        payload_printer=print_payload_fn,
        dispatch_transcript_update_fn=dispatch_transcript_update,
        chat_dispatch_fn=decide_chat_dispatch,
        basic_answer_stream_print_fn=print_streamed_basic_chat_answer,
        run_chat_research_fn=run_chat_research,
        print_chat_result_payload_fn=print_chat_result_payload,
    )
    return runtime_handle_chat_line(
        settings,
        state,
        line,
        llm_provider=llm_provider,
        task_router=task_router,
        runtime=runtime,
        output_writer=output_writer,
        stop_callback=stop_callback,
    )


def print_cli_payload(payload: object, *, as_json: bool, output_writer=None) -> int:
    return runtime_print_cli_payload(
        payload,
        as_json=as_json,
        print_payload_fn=print_payload,
        dispatch_transcript_update_fn=dispatch_transcript_update,
        output_writer=output_writer,
    )
