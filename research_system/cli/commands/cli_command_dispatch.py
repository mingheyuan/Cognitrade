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
    limit: int = 20,
    status_filter: str | None = None,
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
        limit=limit,
        status_filter=status_filter,
        action_node_id=action_node_id,
        dag_id=dag_id,
        subagent_node_id=subagent_node_id,
        orchestrator_cls=orchestrator_cls,
        run_checks_fn=run_checks_fn,
        build_status_fn=build_status_fn,
        run_doctor_fn=run_doctor_fn,
    )
