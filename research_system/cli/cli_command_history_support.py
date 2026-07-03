from __future__ import annotations

from research_system.cli.cli_command_payloads import build_cli_command_payload
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.settings import Settings


def execute_history_command(
    settings: Settings,
    *,
    orchestrator_cls=ResearchOrchestrator,
    limit: int,
    status_filter: str | None,
    **_: object,
) -> object:
    payload = build_cli_command_payload(command="history", limit=limit, status_filter=status_filter)
    with orchestrator_cls(settings) as orchestrator:
        rows = orchestrator.history()
    if payload["status_filter"]:
        rows = [row for row in rows if row.get("status") == payload["status_filter"]]
    return rows[: payload["limit"]]
