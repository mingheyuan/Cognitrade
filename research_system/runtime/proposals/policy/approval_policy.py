from __future__ import annotations

from research_system.runtime.proposals.policy.auto_approval_support import (
    OrchestratorProposalAutoApprovalSupport,
)
from research_system.runtime.proposals.policy.memory_gate_support import (
    OrchestratorProposalMemoryGateSupport,
)
from research_system.runtime.proposals.policy.target_support import (
    OrchestratorProposalTargetSupport,
)


class OrchestratorProposalApprovalPolicy:
    def __init__(self, *, orchestrator_error_cls: type[Exception]) -> None:
        self.target_support = OrchestratorProposalTargetSupport(
            orchestrator_error_cls=orchestrator_error_cls,
        )
        self.memory_gate_support = OrchestratorProposalMemoryGateSupport()
        self.auto_approval_support = OrchestratorProposalAutoApprovalSupport(
            memory_gate_support=self.memory_gate_support,
        )

    def evaluate_methodology_auto_approval(
        self,
        *,
        proposal,
        constraints: dict,
    ) -> dict[str, object]:
        return self.auto_approval_support.evaluate_methodology_auto_approval(
            proposal=proposal,
            constraints=constraints,
        )

    def resolve_methodology_target_run_ids(self, request) -> list[str]:
        return self.target_support.resolve_methodology_target_run_ids(request)

    def stale_memory_support_reason(
        self,
        *,
        proposal,
        max_age_days: int,
    ) -> str | None:
        return self.memory_gate_support.stale_memory_support_reason(
            proposal=proposal,
            max_age_days=max_age_days,
        )

    def insufficient_memory_support_reason(
        self,
        *,
        proposal,
        min_entity_scope_count: int,
    ) -> str | None:
        return self.memory_gate_support.insufficient_memory_support_reason(
            proposal=proposal,
            min_entity_scope_count=min_entity_scope_count,
        )
