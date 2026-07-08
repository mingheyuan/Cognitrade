from __future__ import annotations

from research_system.cli.commands.cli_command_dispatch_registry import CLI_COMMAND_EXECUTORS, resolve_cli_command_executor
from research_system.runtime.platform.checks import run_checks
from research_system.runtime.diagnostics.doctoring.doctor import build_status, run_doctor
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings

def execute_cli_payload_command(
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
    action_node_id: str | None = None,
    dag_id: str | None = None,
    subagent_node_id: str | None = None,
    orchestrator_cls=ResearchOrchestrator,
    run_checks_fn=run_checks,
    build_status_fn=build_status,
    run_doctor_fn=run_doctor,
) -> object:
    executor = resolve_cli_command_executor(command, executors=CLI_COMMAND_EXECUTORS)
    return executor(
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
        action_node_id=action_node_id,
        dag_id=dag_id,
        subagent_node_id=subagent_node_id,
        orchestrator_cls=orchestrator_cls,
        run_checks_fn=run_checks_fn,
        build_status_fn=build_status_fn,
        run_doctor_fn=run_doctor_fn,
    )
