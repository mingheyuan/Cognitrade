from __future__ import annotations

from research_system.cli.application.app_entrypoint import (
    build_parser as package_build_parser,
    execute_cli_command as package_execute_cli_command,
    handle_chat_line as package_handle_chat_line,
    main as package_main,
    print_cli_payload as package_print_cli_payload,
)
from research_system.cli.shell.chat_fallback import run_chat_fallback
from research_system.cli.shell.chat_shell import run_chat_tui
from research_system.cli.state.chat_types import ChatSessionState, TuiSession
from research_system.runtime.orchestration.orchestrator import ResearchOrchestrator
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.runtime.platform.settings import Settings
from research_system.services.routing.router.task_router import ChatDispatchDecision, TaskRouter


def build_parser():
    return package_build_parser()


def main(argv: list[str] | None = None) -> int:
    return package_main(
        argv,
        run_chat_tui_fn=run_chat_tui,
        run_chat_fallback_fn=run_chat_fallback,
        handle_chat_line_fn=_handle_chat_line,
        execute_cli_command_fn=_execute_cli_command,
        print_payload_fn=_print_payload,
    )


def _execute_cli_command(
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
    registration_template_id: str | None = None,
    registration_type: str | None = None,
    skill_name: str | None = None,
    snapshot_type: str | None = None,
    source_id: str | None = None,
    endpoint_path: str | None = None,
    http_method: str | None = None,
    openapi_spec_uri: str | None = None,
    openapi_operation_id: str | None = None,
    timeout_seconds: float | None = None,
) -> object:
    return package_execute_cli_command(
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
        registration_template_id=registration_template_id,
        registration_type=registration_type,
        skill_name=skill_name,
        snapshot_type=snapshot_type,
        source_id=source_id,
        endpoint_path=endpoint_path,
        http_method=http_method,
        openapi_spec_uri=openapi_spec_uri,
        openapi_operation_id=openapi_operation_id,
        timeout_seconds=timeout_seconds,
        orchestrator_cls=ResearchOrchestrator,
    )


def _handle_chat_line(
    settings: Settings,
    state: ChatSessionState,
    line: str,
    *,
    llm_provider: OpenAIProvider,
    task_router: TaskRouter,
    output_writer=None,
    stop_callback=None,
) -> str | None:
    return package_handle_chat_line(
        settings,
        state,
        line,
        llm_provider=llm_provider,
        task_router=task_router,
        output_writer=output_writer,
        stop_callback=stop_callback,
        execute_cli_command_fn=_execute_cli_command,
        print_payload_fn=_print_payload,
    )


def _print_payload(payload: object, *, as_json: bool, output_writer=None) -> int:
    return package_print_cli_payload(
        payload,
        as_json=as_json,
        output_writer=output_writer,
    )


if __name__ == "__main__":
    raise SystemExit(main())
