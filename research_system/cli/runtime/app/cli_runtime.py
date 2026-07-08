from __future__ import annotations

from dataclasses import dataclass

from research_system.runtime.platform.settings import Settings


@dataclass(frozen=True)
class CliCommandRuntime:
    orchestrator_cls: object
    build_research_request_fn: object
    execute_cli_payload_command_fn: object
    run_checks_fn: object
    build_status_fn: object
    run_doctor_fn: object

    def execute(
        self,
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
        timeout_seconds: float | None = None,
    ) -> object:
        if command == "run":
            with self.orchestrator_cls(settings) as orchestrator:
                request = self.build_research_request_fn(
                    query=query or "",
                    intent=intent,
                    review_target_run_id=run_id,
                )
                response = orchestrator.run(request)
                return response.model_dump(mode="json")
        return self.execute_cli_payload_command_fn(
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
            timeout_seconds=timeout_seconds,
            orchestrator_cls=self.orchestrator_cls,
            run_checks_fn=self.run_checks_fn,
            build_status_fn=self.build_status_fn,
            run_doctor_fn=self.run_doctor_fn,
        )


def build_cli_command_runtime(
    *,
    orchestrator_cls,
    build_research_request_fn,
    execute_cli_payload_command_fn,
    run_checks_fn,
    build_status_fn,
    run_doctor_fn,
) -> CliCommandRuntime:
    return CliCommandRuntime(
        orchestrator_cls=orchestrator_cls,
        build_research_request_fn=build_research_request_fn,
        execute_cli_payload_command_fn=execute_cli_payload_command_fn,
        run_checks_fn=run_checks_fn,
        build_status_fn=build_status_fn,
        run_doctor_fn=run_doctor_fn,
    )


def execute_cli_command(
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
    timeout_seconds: float | None = None,
    runtime: CliCommandRuntime,
) -> object:
    return runtime.execute(
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
        timeout_seconds=timeout_seconds,
    )
