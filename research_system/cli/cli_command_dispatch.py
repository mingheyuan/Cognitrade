from __future__ import annotations

from pathlib import Path

from research_system.cli.cli_command_payloads import PROPOSAL_COMMANDS, RUN_ID_COMMANDS, STATUS_FILTER_COMMANDS, build_cli_command_payload
from research_system.runtime.checks import run_checks
from research_system.runtime.doctor import build_status, run_doctor
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.settings import Settings


def _execute_history_command(
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


def _execute_proposals_command(
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


def _execute_proposal_detail_command(
    settings: Settings,
    *,
    orchestrator_cls=ResearchOrchestrator,
    run_id: str | None,
    **_: object,
) -> object:
    payload = build_cli_command_payload(command="proposal", proposal_id=run_id)
    with orchestrator_cls(settings) as orchestrator:
        return orchestrator.show_proposal(payload["run_id"])


def _execute_review_proposal_command(
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


def _execute_run_id_command(
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
        if command == "resume":
            return orchestrator.resume(payload["run_id"])
        return orchestrator.cancel(payload["run_id"])


def _execute_cleanup_command(
    settings: Settings,
    **_: object,
) -> object:
    orchestrator_cls = _.get("orchestrator_cls", ResearchOrchestrator)
    with orchestrator_cls(settings) as orchestrator:
        return orchestrator.cleanup()


def _execute_reindex_command(
    settings: Settings,
    **_: object,
) -> object:
    orchestrator_cls = _.get("orchestrator_cls", ResearchOrchestrator)
    with orchestrator_cls(settings) as orchestrator:
        return orchestrator.rebuild_qdrant()


def _execute_check_command(
    settings: Settings,
    **_: object,
) -> object:
    run_checks_fn = _.get("run_checks_fn", run_checks)
    return run_checks_fn(Path.cwd())


def _execute_status_command(
    settings: Settings,
    **_: object,
) -> object:
    build_status_fn = _.get("build_status_fn", build_status)
    return build_status_fn(settings)


def _execute_doctor_command(
    settings: Settings,
    **_: object,
) -> object:
    run_doctor_fn = _.get("run_doctor_fn", run_doctor)
    return run_doctor_fn(settings)


_COMMAND_EXECUTORS = {
    "history": _execute_history_command,
    "proposals": _execute_proposals_command,
    "proposal": _execute_proposal_detail_command,
    "review-proposal": _execute_review_proposal_command,
    "replay": _execute_run_id_command,
    "resume": _execute_run_id_command,
    "cancel": _execute_run_id_command,
    "cleanup": _execute_cleanup_command,
    "reindex-qdrant": _execute_reindex_command,
    "check": _execute_check_command,
    "status": _execute_status_command,
    "doctor": _execute_doctor_command,
}


def execute_cli_payload_command(
    settings: Settings,
    *,
    command: str,
    query: str | None = None,
    intent: str | None = None,
    run_id: str | None = None,
    limit: int = 20,
    status_filter: str | None = None,
    orchestrator_cls=ResearchOrchestrator,
    run_checks_fn=run_checks,
    build_status_fn=build_status,
    run_doctor_fn=run_doctor,
) -> object:
    if command in STATUS_FILTER_COMMANDS and command not in _COMMAND_EXECUTORS:
        raise ValueError(f"missing status command executor: {command}")
    if command in RUN_ID_COMMANDS and command not in _COMMAND_EXECUTORS:
        raise ValueError(f"missing run-id command executor: {command}")
    if command in PROPOSAL_COMMANDS and command not in _COMMAND_EXECUTORS:
        raise ValueError(f"missing proposal command executor: {command}")
    executor = _COMMAND_EXECUTORS.get(command)
    if executor is None:
        raise ValueError(f"unsupported command: {command}")
    return executor(
        settings,
        command=command,
        query=query,
        intent=intent,
        run_id=run_id,
        limit=limit,
        status_filter=status_filter,
        orchestrator_cls=orchestrator_cls,
        run_checks_fn=run_checks_fn,
        build_status_fn=build_status_fn,
        run_doctor_fn=run_doctor_fn,
    )
