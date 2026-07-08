from __future__ import annotations

STATUS_FILTER_COMMANDS = frozenset({"history", "status", "doctor", "check", "cleanup", "reindex-qdrant"})
RUN_ID_COMMANDS = frozenset({"replay", "resume", "cancel", "trace", "events", "run-steps", "metrics"})
RUN_ID_QUERY_COMMANDS = frozenset({"subagent-read", "run-thread", "rerun-from"})
RUN_ID_SELECTOR_COMMANDS = frozenset({"rerun-subagent"})
SUBAGENT_COMMANDS = frozenset({"subagent-read"})
PROPOSAL_COMMANDS = frozenset({"proposals", "proposal", "review-proposal"})
SUBAGENT_DISCOVERY_COMMANDS = frozenset(
    {
        "subagent-discoveries",
        "subagent-discovery",
        "review-subagent-discovery",
        "activate-subagent-discovery",
    }
)


def build_cli_command_payload(
    *,
    command: str,
    run_id: str | None = None,
    limit: int = 20,
    status_filter: str | None = None,
    proposal_id: str | None = None,
    ticket_id: str | None = None,
    query: str | None = None,
    decision: str | None = None,
    reviewer: str | None = None,
    reason: str | None = None,
    status: str | None = None,
    activated_by: str | None = None,
    delegate_subagent_type: str | None = None,
    candidate_subagent_type: str | None = None,
    registration_template_id: str | None = None,
    registration_type: str | None = None,
    skill_name: str | None = None,
    snapshot_type: str | None = None,
    source_id: str | None = None,
    tool_name: str | None = None,
    endpoint_path: str | None = None,
    http_method: str | None = None,
    openapi_spec_uri: str | None = None,
    openapi_operation_id: str | None = None,
    timeout_seconds: float | None = None,
    action_node_id: str | None = None,
    dag_id: str | None = None,
    subagent_node_id: str | None = None,
) -> dict:
    if command in STATUS_FILTER_COMMANDS:
        return {
            "command": command,
            "limit": int(limit),
            "status_filter": status_filter,
        }
    if command in RUN_ID_COMMANDS:
        return {
            "command": command,
            "run_id": str(run_id or "").strip(),
        }
    if command in RUN_ID_QUERY_COMMANDS:
        return {
            "command": command,
            "run_id": str(run_id or "").strip(),
            "query": str(query if query is not None else decision or "").strip(),
        }
    if command in RUN_ID_SELECTOR_COMMANDS:
        return {
            "command": command,
            "run_id": str(run_id or "").strip(),
            "action_node_id": str(action_node_id or "").strip(),
            "dag_id": str(dag_id or "").strip(),
            "subagent_node_id": str(subagent_node_id or "").strip(),
        }
    if command == "proposals":
        return {
            "command": "proposals",
            "limit": int(limit),
            "status_filter": status,
        }
    if command == "proposal":
        return {
            "command": "proposal",
            "run_id": str(proposal_id or "").strip(),
        }
    if command == "review-proposal":
        return {
            "command": "review-proposal",
            "run_id": str(proposal_id or "").strip(),
            "query": str(decision or "").strip(),
            "intent": str(reviewer or "").strip() or "manual_cli_review",
            "status_filter": reason,
        }
    if command == "subagent-discoveries":
        return {
            "command": "subagent-discoveries",
            "limit": int(limit),
            "status_filter": status,
        }
    if command == "subagent-discovery":
        return {
            "command": "subagent-discovery",
            "ticket_id": str(ticket_id or "").strip(),
        }
    if command == "review-subagent-discovery":
        return {
            "command": "review-subagent-discovery",
            "ticket_id": str(ticket_id or "").strip(),
            "query": str(decision or "").strip(),
            "intent": str(reviewer or "").strip() or "manual_cli_review",
            "status_filter": reason,
        }
    if command == "activate-subagent-discovery":
        return {
            "command": "activate-subagent-discovery",
            "ticket_id": str(ticket_id or "").strip(),
            "query": str(delegate_subagent_type or query or "").strip(),
            "intent": str(activated_by or "").strip() or "manual_cli_activation",
            "status_filter": reason,
            "subagent_node_id": str(candidate_subagent_type or subagent_node_id or "").strip(),
            "registration_template_id": str(registration_template_id or "").strip(),
            "registration_type": str(registration_type or "").strip(),
            "skill_name": str(skill_name or "").strip(),
            "snapshot_type": str(snapshot_type or "").strip(),
            "source_id": str(source_id or "").strip(),
            "tool_name": str(tool_name or "").strip(),
            "endpoint_path": str(endpoint_path or "").strip(),
            "http_method": str(http_method or "").strip().upper(),
            "openapi_spec_uri": str(openapi_spec_uri or "").strip(),
            "openapi_operation_id": str(openapi_operation_id or "").strip(),
            "timeout_seconds": float(timeout_seconds) if timeout_seconds is not None else None,
        }
    raise ValueError(f"unsupported cli command payload: {command}")
