from __future__ import annotations

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


class OrchestratorProposalCollectSupport:
    def __init__(self, *, host) -> None:
        self.host = host

    def run_methodology_evolution_collect(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
    ) -> list[BlackboardEntry]:
        target_run_ids = self.host.resolve_methodology_target_run_ids(request)
        inputs = self.host.evolution_input_support.collect_inputs(
            request=request,
            target_run_ids=target_run_ids,
        )
        candidates = list(inputs.get("candidates") or [])
        proposals = self.host.proposal_generator.build(author_agent=agent, candidates=candidates)
        auto_approve = bool(request.constraints.get("auto_approve", True))
        entries: list[BlackboardEntry] = [
            self.host.entry_factory.build_methodology_evolution_request_entry(
                context=context,
                request=request,
                phase=phase,
                agent=agent,
                target_run_ids=target_run_ids,
                candidates=candidates,
                proposals=proposals,
            )
        ]

        for proposal in proposals:
            previous_status = proposal.status
            self.host.proposal_store.create(proposal)
            approval_decision, apply_outputs = self.host.review_pipeline.process_methodology_evolution_proposal(
                proposal=proposal,
                constraints=request.constraints,
                previous_status=previous_status,
                auto_approve=auto_approve,
            )
            entries.append(
                self.host.entry_factory.build_proposal_draft_entry(
                    context=context,
                    request=request,
                    phase=phase,
                    agent=agent,
                    proposal=proposal,
                    target_run_ids=target_run_ids,
                    approval_decision=approval_decision,
                    apply_outputs=apply_outputs,
                )
            )
        return entries


__all__ = ["OrchestratorProposalCollectSupport"]
