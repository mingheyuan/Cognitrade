from __future__ import annotations

STATUS_FILTER_COMMANDS = frozenset({"history", "status", "doctor", "check", "cleanup", "reindex-qdrant"})
RUN_ID_COMMANDS = frozenset({"replay", "resume", "cancel"})
PROPOSAL_COMMANDS = frozenset({"proposals", "proposal", "review-proposal"})


def build_cli_command_payload(
    *,
    command: str,
    run_id: str | None = None,
    limit: int = 20,
    status_filter: str | None = None,
    proposal_id: str | None = None,
    decision: str | None = None,
    reviewer: str | None = None,
    reason: str | None = None,
    status: str | None = None,
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
    raise ValueError(f"unsupported cli command payload: {command}")
