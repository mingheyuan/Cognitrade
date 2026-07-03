from __future__ import annotations

from research_system.runtime.orchestration.orchestrator_research_debug import (
    OrchestratorDebugSupport,
)
from research_system.runtime.methodology.support import OrchestratorMethodologySupport
from research_system.runtime.proposals.support import OrchestratorProposalSupport


class OrchestratorResearchSupport:
    def __init__(
        self,
        *,
        run_store,
        comps_service,
        dcf_service,
        cashflow_resilience_service,
        proposal_store,
        methodology_evolver,
        proposal_generator,
        methodology_manifest_service,
        retrieval_policy_service,
        memory_service,
        methodology_memory_writer,
        orchestrator_error_cls: type[Exception],
    ) -> None:
        self.debug = OrchestratorDebugSupport()
        self.methodology = OrchestratorMethodologySupport(
            comps_service=comps_service,
            dcf_service=dcf_service,
            cashflow_resilience_service=cashflow_resilience_service,
        )
        self.proposals = OrchestratorProposalSupport(
            run_store=run_store,
            proposal_store=proposal_store,
            methodology_evolver=methodology_evolver,
            proposal_generator=proposal_generator,
            methodology_manifest_service=methodology_manifest_service,
            retrieval_policy_service=retrieval_policy_service,
            memory_service=memory_service,
            methodology_memory_writer=methodology_memory_writer,
            orchestrator_error_cls=orchestrator_error_cls,
        )

    def build_debug_payload(self, **kwargs) -> dict:
        return self.debug.build_debug_payload(**kwargs)

    def build_candidate_methodology_entries(self, **kwargs):
        return self.methodology.build_candidate_methodology_entries(**kwargs)

    def build_stock_methodology_entries(self, **kwargs):
        return self.methodology.build_stock_methodology_entries(**kwargs)

    def build_review_methodology_entries(self, **kwargs):
        return self.methodology.build_review_methodology_entries(**kwargs)

    def run_methodology_evolution_collect(self, **kwargs):
        return self.proposals.run_methodology_evolution_collect(**kwargs)

    def persist_proposal_transition(self, proposal, *, previous_status: str, apply_approved_side_effects: bool):
        return self.proposals.persist_proposal_transition(
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
        self.proposals.append_proposal_review_event(
            proposal,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            previous_status=previous_status,
            reviewed_at=reviewed_at,
            source=source,
        )

    def evaluate_methodology_auto_approval(self, *, proposal, constraints: dict) -> dict[str, object]:
        return self.proposals.evaluate_methodology_auto_approval(proposal=proposal, constraints=constraints)

    def resolve_methodology_target_run_ids(self, request) -> list[str]:
        return self.proposals.resolve_methodology_target_run_ids(request)

    def stale_memory_support_reason(self, *, proposal, max_age_days: int) -> str | None:
        return self.proposals.stale_memory_support_reason(proposal=proposal, max_age_days=max_age_days)

    def insufficient_memory_support_reason(self, *, proposal, min_entity_scope_count: int) -> str | None:
        return self.proposals.insufficient_memory_support_reason(
            proposal=proposal,
            min_entity_scope_count=min_entity_scope_count,
        )
