from __future__ import annotations

from research_system.cli.commands.cli_command_payloads import build_cli_command_payload
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings


def execute_run_id_command(
    settings: Settings,
    *,
    command: str,
    orchestrator_cls=ResearchOrchestrator,
    run_id: str | None,
    **_: object,
) -> object:
    payload = build_cli_command_payload(command=command, run_id=run_id)
    with orchestrator_cls(settings) as orchestrator:
        if command == "replay":
            return orchestrator.replay(payload["run_id"])
        if command == "events":
            return orchestrator.events(payload["run_id"])
        if command == "run-steps":
            return orchestrator.steps(payload["run_id"])
        if command == "resume":
            return orchestrator.resume(payload["run_id"])
        if command == "trace":
            return orchestrator.trace(payload["run_id"])
        return orchestrator.cancel(payload["run_id"])


def execute_subagent_command(
    settings: Settings,
    *,
    command: str,
    orchestrator_cls=ResearchOrchestrator,
    run_id: str | None,
    query: str | None,
    **_: object,
) -> object:
    payload = build_cli_command_payload(command=command, run_id=run_id, decision=query)
    with orchestrator_cls(settings) as orchestrator:
        return orchestrator.subagent(payload["run_id"], payload["query"])
