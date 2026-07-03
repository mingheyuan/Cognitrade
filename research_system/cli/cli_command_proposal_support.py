from __future__ import annotations

from research_system.cli.cli_command_payloads import build_cli_command_payload
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.settings import Settings


def execute_proposals_command(
    settings: Settings,
    *,
    orchestrator_cls=ResearchOrchestrator,
    limit: int,
    status_filter: str | None,
    **_: object,
) -> object:
    payload = build_cli_command_payload(command="proposals", limit=limit, status=status_filter)
    with orchestrator_cls(settings) as orchestrator:
        return orchestrator.list_proposals(status=payload["status_filter"], limit=payload["limit"])


def execute_proposal_detail_command(
    settings: Settings,
    *,
    orchestrator_cls=ResearchOrchestrator,
    run_id: str | None,
    **_: object,
) -> object:
    payload = build_cli_command_payload(command="proposal", proposal_id=run_id)
    with orchestrator_cls(settings) as orchestrator:
        return orchestrator.show_proposal(payload["run_id"])


def execute_review_proposal_command(
    settings: Settings,
    *,
    orchestrator_cls=ResearchOrchestrator,
    run_id: str | None,
    query: str | None,
    intent: str | None,
    status_filter: str | None,
    **_: object,
) -> object:
    payload = build_cli_command_payload(
        command="review-proposal",
        proposal_id=run_id,
        decision=query,
        reviewer=intent,
        reason=status_filter,
    )
    with orchestrator_cls(settings) as orchestrator:
        return orchestrator.review_proposal(
            payload["run_id"],
            decision=payload["query"],
            reviewer=payload["intent"],
            reason=payload["status_filter"],
        )
