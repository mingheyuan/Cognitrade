from __future__ import annotations

from typing import Any

from research_system.schemas.proposals import ProposalDraft
from research_system.services.memory.core.service import MemoryService


class MethodologyMemoryWriter:
    def __init__(self, memory_service: MemoryService) -> None:
        self.memory_service = memory_service

    def write_from_proposal(self, proposal: ProposalDraft) -> dict[str, Any]:
        memory_type = {
            "MethodologyTemplatePatch": "methodology_summary",
            "ValuationHeuristicPatch": "dimension_experience",
            "MemoryLearningUpdate": "error_pattern",
        }.get(proposal.proposal_type, "methodology_summary")
        return self.memory_service.write_methodology_memory(
            memory_type=memory_type,
            content={
                "proposal_id": proposal.proposal_id,
                "proposal_type": proposal.proposal_type,
                "title": proposal.title,
                "summary": proposal.summary,
                "benchmark_scope": proposal.benchmark_scope,
                "sample_size": proposal.sample_size,
                "reviewer": proposal.reviewer,
                "review_reason": proposal.review_reason,
                "created_from_run_id": proposal.created_from_run_id,
                "created_from_review_id": proposal.created_from_review_id,
                "reviewed_at": proposal.reviewed_at.isoformat() if proposal.reviewed_at else None,
                "after_metrics": proposal.after_metrics,
                "evidence_summary": proposal.evidence_summary,
            },
            metadata={
                "status": proposal.status,
                "author_agent": proposal.author_agent,
                "created_from_run_id": proposal.created_from_run_id,
                "proposal_id": proposal.proposal_id,
                "proposal_type": proposal.proposal_type,
                "benchmark_scope": proposal.benchmark_scope,
                "sample_size": proposal.sample_size,
                "reviewer": proposal.reviewer,
                "review_reason": proposal.review_reason,
                "reviewed_at": proposal.reviewed_at.isoformat() if proposal.reviewed_at else None,
            },
        )


__all__ = ["MethodologyMemoryWriter"]
