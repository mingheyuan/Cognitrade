from __future__ import annotations

from research_system.cli.commands.cli_command_history_support import execute_history_command
from research_system.cli.commands.cli_command_payloads import (
    PROPOSAL_COMMANDS,
    RUN_ID_COMMANDS,
    RUN_ID_QUERY_COMMANDS,
    RUN_ID_SELECTOR_COMMANDS,
    STATUS_FILTER_COMMANDS,
    SUBAGENT_COMMANDS,
)
from research_system.cli.commands.cli_command_proposal_support import (
    execute_proposal_detail_command,
    execute_proposals_command,
    execute_review_proposal_command,
)
from research_system.cli.commands.cli_command_run_support import execute_run_id_command, execute_run_query_command
from research_system.cli.commands.cli_command_system_support import (
    execute_check_command,
    execute_cleanup_command,
    execute_doctor_command,
    execute_reindex_command,
    execute_status_command,
)


def build_cli_command_executors() -> dict[str, object]:
    return {
        "history": execute_history_command,
        "proposals": execute_proposals_command,
        "proposal": execute_proposal_detail_command,
        "review-proposal": execute_review_proposal_command,
        "replay": execute_run_id_command,
        "events": execute_run_id_command,
        "run-steps": execute_run_id_command,
        "metrics": execute_run_id_command,
        "subagent-read": execute_run_query_command,
        "run-thread": execute_run_query_command,
        "rerun-from": execute_run_query_command,
        "rerun-subagent": execute_run_query_command,
        "trace": execute_run_id_command,
        "resume": execute_run_id_command,
        "cancel": execute_run_id_command,
        "cleanup": execute_cleanup_command,
        "reindex-qdrant": execute_reindex_command,
        "check": execute_check_command,
        "status": execute_status_command,
        "doctor": execute_doctor_command,
    }


CLI_COMMAND_EXECUTORS = build_cli_command_executors()


def resolve_cli_command_executor(command: str, *, executors: dict[str, object] | None = None):
    resolved_executors = executors or CLI_COMMAND_EXECUTORS
    if command in STATUS_FILTER_COMMANDS and command not in resolved_executors:
        raise ValueError(f"missing status command executor: {command}")
    if command in RUN_ID_COMMANDS and command not in resolved_executors:
        raise ValueError(f"missing run-id command executor: {command}")
    if command in RUN_ID_QUERY_COMMANDS and command not in resolved_executors:
        raise ValueError(f"missing run-id query command executor: {command}")
    if command in RUN_ID_SELECTOR_COMMANDS and command not in resolved_executors:
        raise ValueError(f"missing run-id selector command executor: {command}")
    if command in SUBAGENT_COMMANDS and command not in resolved_executors:
        raise ValueError(f"missing subagent command executor: {command}")
    if command in PROPOSAL_COMMANDS and command not in resolved_executors:
        raise ValueError(f"missing proposal command executor: {command}")
    executor = resolved_executors.get(command)
    if executor is None:
        raise ValueError(f"unsupported command: {command}")
    return executor
