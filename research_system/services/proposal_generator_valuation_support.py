from __future__ import annotations

from typing import Any

from research_system.schemas.proposals import ProposalDraft


class ProposalGeneratorValuationSupport:
    def build(self, *, author_agent: str, candidate: dict[str, Any]) -> ProposalDraft:
        title = str(candidate.get("title") or "valuation_heuristic")
        evidence_summary_payload = candidate.get("aggregated_evidence_summary") or candidate.get("evidence_summary") or {}
        sample_size = int(candidate.get("sample_size") or max(len(candidate.get("related_run_ids") or []), 1))
        return ProposalDraft(
            proposal_type="ValuationHeuristicPatch",
            title=f"Update valuation heuristic: {title}",
            summary=f"Capture valuation heuristic from {candidate.get('methodology_type')} output.",
            created_from_run_id=str(candidate.get("run_id") or ""),
            author_agent=author_agent,
            sample_size=max(sample_size, 1),
            benchmark_scope=str(candidate.get("methodology_type") or "valuation"),
            before_metrics={
                "assumption_tags": list(candidate.get("assumption_tags") or []),
                "average_confidence": candidate.get("confidence"),
                "memory_match_score": candidate.get("memory_match_score"),
                "memory_support": dict(candidate.get("memory_support") or {}),
            },
            after_metrics={
                "sensitivity_tags": list(candidate.get("sensitivity_tags") or []),
                "aggregated_evidence_summary": evidence_summary_payload,
            },
            evidence_summary=str(evidence_summary_payload),
            related_entry_ids=[
                str(item)
                for item in (candidate.get("related_entry_ids") or [candidate.get("entry_id")])
                if str(item or "").strip()
            ],
        )


__all__ = ["ProposalGeneratorValuationSupport"]
