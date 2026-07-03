from __future__ import annotations

from research_system.runtime.orchestrator_context import RuntimeContext
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


class OrchestratorProposalEntryFactory:
    def build_methodology_evolution_request_entry(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
        target_run_ids: list[str],
        candidates: list[dict],
        proposals: list[object],
    ) -> BlackboardEntry:
        return BlackboardEntry(
            run_id=context.run_id,
            request_id=request.request_id,
            kind="source_record",
            agent=agent,
            phase=phase,
            payload={
                "title": "methodology_evolution_request",
                "content": request.user_query,
                "source_type": "methodology_evolution_request",
                "target_run_id": target_run_ids[0] if len(target_run_ids) == 1 else None,
                "target_run_ids": target_run_ids,
                "candidate_count": len(candidates),
                "proposal_count": len(proposals),
            },
            source_refs=[
                {
                    "type": "request",
                    "target_run_id": target_run_ids[0] if len(target_run_ids) == 1 else None,
                    "target_run_ids": target_run_ids,
                    "run_id": context.run_id,
                }
            ],
        )

    def build_proposal_draft_entry(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
        proposal,
        target_run_ids: list[str],
        approval_decision: dict[str, object],
        apply_outputs: dict[str, object],
    ) -> BlackboardEntry:
        return BlackboardEntry(
            run_id=context.run_id,
            request_id=request.request_id,
            kind="proposal_draft",
            agent=agent,
            phase=phase,
            payload={
                "title": proposal.title,
                "source_type": "proposal_draft",
                "proposal": proposal.model_dump(mode="json"),
                "approval_decision": approval_decision,
                **apply_outputs,
            },
            source_refs=[
                {
                    "type": "methodology_evolution",
                    "target_run_id": target_run_ids[0] if len(target_run_ids) == 1 else None,
                    "target_run_ids": target_run_ids,
                    "run_id": context.run_id,
                }
            ],
            confidence=1.0,
            risk_flags=[],
        )
