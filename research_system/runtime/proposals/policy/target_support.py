from __future__ import annotations


class OrchestratorProposalTargetSupport:
    def __init__(self, *, orchestrator_error_cls: type[Exception]) -> None:
        self.orchestrator_error_cls = orchestrator_error_cls

    def resolve_methodology_target_run_ids(self, request) -> list[str]:
        raw_target_run_ids = list(request.constraints.get("target_run_ids") or [])
        normalized: list[str] = []
        for item in raw_target_run_ids:
            run_id = str(item or "").strip()
            if run_id and run_id not in normalized:
                normalized.append(run_id)
        target_run_id = str(request.constraints.get("target_run_id") or "").strip()
        if target_run_id and target_run_id not in normalized:
            normalized.append(target_run_id)
        if not normalized:
            raise self.orchestrator_error_cls(
                "methodology_evolution requires constraints.target_run_id or constraints.target_run_ids"
            )
        return normalized
