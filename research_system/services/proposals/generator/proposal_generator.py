from __future__ import annotations

from typing import Any

from research_system.schemas.proposals import ProposalDraft
from research_system.services.proposals.generator.proposal_generator_dimension_support import (
    ProposalGeneratorDimensionSupport,
)
from research_system.services.proposals.generator.proposal_generator_review_support import (
    ProposalGeneratorReviewSupport,
)
from research_system.services.proposals.generator.proposal_generator_valuation_support import (
    ProposalGeneratorValuationSupport,
)


class ProposalGenerator:
    def __init__(self) -> None:
        self.dimension_support = ProposalGeneratorDimensionSupport()
        self.valuation_support = ProposalGeneratorValuationSupport()
        self.review_support = ProposalGeneratorReviewSupport()

    def build(self, *, author_agent: str, candidates: list[dict[str, Any]]) -> list[ProposalDraft]:
        proposals: list[ProposalDraft] = []
        for candidate in candidates:
            candidate_type = str(candidate.get("candidate_type") or "")
            if candidate_type == "dimension":
                proposals.append(self.dimension_support.build(author_agent=author_agent, candidate=candidate))
            elif candidate_type == "valuation_heuristic":
                proposals.append(self.valuation_support.build(author_agent=author_agent, candidate=candidate))
            elif candidate_type == "review_pattern":
                proposals.append(self.review_support.build(author_agent=author_agent, candidate=candidate))
        return proposals
