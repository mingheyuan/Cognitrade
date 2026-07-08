from __future__ import annotations

from research_system.cli.commands.cli_command_payloads import build_cli_command_payload
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings


def execute_subagent_discoveries_command(
    settings: Settings,
    *,
    orchestrator_cls=ResearchOrchestrator,
    limit: int,
    status_filter: str | None,
    **_: object,
) -> object:
    payload = build_cli_command_payload(command="subagent-discoveries", limit=limit, status=status_filter)
    with orchestrator_cls(settings) as orchestrator:
        return orchestrator.list_subagent_discovery_requests(
            status=payload["status_filter"],
            limit=payload["limit"],
        )


def execute_subagent_discovery_detail_command(
    settings: Settings,
    *,
    orchestrator_cls=ResearchOrchestrator,
    ticket_id: str | None,
    **_: object,
) -> object:
    payload = build_cli_command_payload(command="subagent-discovery", ticket_id=ticket_id)
    with orchestrator_cls(settings) as orchestrator:
        return orchestrator.show_subagent_discovery_request(payload["ticket_id"])


def execute_review_subagent_discovery_command(
    settings: Settings,
    *,
    orchestrator_cls=ResearchOrchestrator,
    ticket_id: str | None,
    query: str | None,
    intent: str | None,
    status_filter: str | None,
    **_: object,
) -> object:
    payload = build_cli_command_payload(
        command="review-subagent-discovery",
        ticket_id=ticket_id,
        decision=query,
        reviewer=intent,
        reason=status_filter,
    )
    with orchestrator_cls(settings) as orchestrator:
        return orchestrator.review_subagent_discovery_request(
            payload["ticket_id"],
            decision=payload["query"],
            reviewer=payload["intent"],
            reason=payload["status_filter"],
        )


def execute_activate_subagent_discovery_command(
    settings: Settings,
    *,
    orchestrator_cls=ResearchOrchestrator,
    ticket_id: str | None,
    query: str | None,
    intent: str | None,
    activated_by: str | None,
    status_filter: str | None,
    candidate_subagent_type: str | None,
    subagent_node_id: str | None,
    registration_template_id: str | None,
    registration_type: str | None,
    skill_name: str | None,
    snapshot_type: str | None,
    source_id: str | None,
    tool_name: str | None,
    endpoint_path: str | None,
    http_method: str | None,
    openapi_spec_uri: str | None,
    openapi_operation_id: str | None,
    timeout_seconds: float | None,
    **_: object,
) -> object:
    payload = build_cli_command_payload(
        command="activate-subagent-discovery",
        ticket_id=ticket_id,
        delegate_subagent_type=query,
        activated_by=activated_by or intent,
        reason=status_filter,
        candidate_subagent_type=candidate_subagent_type or subagent_node_id,
        registration_template_id=registration_template_id,
        registration_type=registration_type,
        skill_name=skill_name,
        snapshot_type=snapshot_type,
        source_id=source_id,
        tool_name=tool_name,
        endpoint_path=endpoint_path,
        http_method=http_method,
        openapi_spec_uri=openapi_spec_uri,
        openapi_operation_id=openapi_operation_id,
        timeout_seconds=timeout_seconds,
    )
    with orchestrator_cls(settings) as orchestrator:
        return orchestrator.activate_subagent_discovery_request(
            payload["ticket_id"],
            delegate_subagent_type=payload["query"] or None,
            candidate_subagent_type=payload["subagent_node_id"] or None,
            registration_template_id=payload["registration_template_id"] or None,
            activated_by=payload["intent"],
            reason=payload["status_filter"],
            registration_type=payload["registration_type"] or None,
            skill_name=payload["skill_name"] or None,
            snapshot_type=payload["snapshot_type"] or None,
            source_id=payload["source_id"] or None,
            tool_name=payload["tool_name"] or None,
            endpoint_path=payload["endpoint_path"] or None,
            http_method=payload["http_method"] or None,
            openapi_spec_uri=payload["openapi_spec_uri"] or None,
            openapi_operation_id=payload["openapi_operation_id"] or None,
            timeout_seconds=payload["timeout_seconds"],
        )
