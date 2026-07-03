from __future__ import annotations

from research_system.runtime.orchestrator_context import RuntimeContext
from research_system.runtime.orchestrator_methodology_evolution_input_support import (
    OrchestratorMethodologyEvolutionInputSupport,
)
from research_system.runtime.orchestrator_proposal_collect_support import (
    OrchestratorProposalCollectSupport,
)
from research_system.runtime.orchestrator_proposal_entry_factory import (
    OrchestratorProposalEntryFactory,
)
from research_system.runtime.orchestrator_proposal_approval_policy import (
    OrchestratorProposalApprovalPolicy,
)
from research_system.runtime.orchestrator_proposal_review_pipeline import (
    OrchestratorProposalReviewPipeline,
)
from research_system.runtime.orchestrator_proposal_transition_support import (
    OrchestratorProposalTransitionSupport,
)
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


class OrchestratorProposalSupport:
    def __init__(
        self,
        *,
        run_store,
        proposal_store,
        methodology_evolver,
        proposal_generator,
        methodology_manifest_service,
        retrieval_policy_service,
        memory_service,
        methodology_memory_writer,
        orchestrator_error_cls: type[Exception],
    ) -> None:
        self.run_store = run_store
        self.proposal_store = proposal_store
        self.proposal_generator = proposal_generator
        self.evolution_input_support = OrchestratorMethodologyEvolutionInputSupport(
            run_store=run_store,
            methodology_evolver=methodology_evolver,
            memory_service=memory_service,
        )
        self.entry_factory = OrchestratorProposalEntryFactory()
        self.transition_support = OrchestratorProposalTransitionSupport(
            proposal_store=proposal_store,
            methodology_manifest_service=methodology_manifest_service,
            retrieval_policy_service=retrieval_policy_service,
            methodology_memory_writer=methodology_memory_writer,
        )
        self.approval_policy = OrchestratorProposalApprovalPolicy(
            orchestrator_error_cls=orchestrator_error_cls,
        )
        self.review_pipeline = OrchestratorProposalReviewPipeline(host=self)
        self.collect_support = OrchestratorProposalCollectSupport(host=self)

    def run_methodology_evolution_collect(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
    ) -> list[BlackboardEntry]:
        return self.collect_support.run_methodology_evolution_collect(
            context=context,
            request=request,
            phase=phase,
            agent=agent,
        )

    def persist_proposal_transition(
        self,
        proposal,
        *,
        previous_status: str,
        apply_approved_side_effects: bool,
    ) -> dict[str, object]:
        return self.transition_support.persist_proposal_transition(
            proposal,
            previous_status=previous_status,
            apply_approved_side_effects=apply_approved_side_effects,
        )

    def append_proposal_review_event(
        self,
        proposal,
        *,
        decision: str,
        reviewer: str,
        reason: str,
        previous_status: str | None,
        reviewed_at,
        source: str,
    ) -> None:
        self.transition_support.append_proposal_review_event(
            proposal,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            previous_status=previous_status,
            reviewed_at=reviewed_at,
            source=source,
        )

    def evaluate_methodology_auto_approval(
        self,
        *,
        proposal,
        constraints: dict,
    ) -> dict[str, object]:
        return self.approval_policy.evaluate_methodology_auto_approval(
            proposal=proposal,
            constraints=constraints,
        )

    def resolve_methodology_target_run_ids(self, request: RequestEnvelope) -> list[str]:
        return self.approval_policy.resolve_methodology_target_run_ids(request)

    def stale_memory_support_reason(
        self,
        *,
        proposal,
        max_age_days: int,
    ) -> str | None:
        return self.approval_policy.stale_memory_support_reason(
            proposal=proposal,
            max_age_days=max_age_days,
        )

    def insufficient_memory_support_reason(
        self,
        *,
        proposal,
        min_entity_scope_count: int,
    ) -> str | None:
        return self.approval_policy.insufficient_memory_support_reason(
            proposal=proposal,
            min_entity_scope_count=min_entity_scope_count,
        )

    def _stale_memory_support_reason(
        self,
        *,
        proposal,
        max_age_days: int,
    ) -> str | None:
        return self.stale_memory_support_reason(proposal=proposal, max_age_days=max_age_days)

    def _insufficient_memory_support_reason(
        self,
        *,
        proposal,
        min_entity_scope_count: int,
    ) -> str | None:
        return self.insufficient_memory_support_reason(
            proposal=proposal,
            min_entity_scope_count=min_entity_scope_count,
        )
