from __future__ import annotations

from typing import Any

from research_system.schemas.proposals import ProposalDraft


class ProposalGeneratorDimensionSupport:
    def build(self, *, author_agent: str, candidate: dict[str, Any]) -> ProposalDraft:
        title = str(candidate.get("title") or "dimension_candidate")
        evidence_summary_payload = candidate.get("aggregated_evidence_summary") or candidate.get("evidence_summary") or {}
        sample_size = int(
            candidate.get("sample_size")
            or max(len(candidate.get("related_run_ids") or []), 0)
            or max(len((candidate.get("evidence_summary") or {}).get("supported_claim_block_ids") or []), 1)
        )
        return ProposalDraft(
            proposal_type="MethodologyTemplatePatch",
            title=f"Promote dimension: {title}",
            summary=f"Promote distilled dimension `{title}` into methodology candidate pool.",
            created_from_run_id=str(candidate.get("run_id") or ""),
            author_agent=author_agent,
            sample_size=max(sample_size, 1),
            benchmark_scope=str(candidate.get("dimension_family") or "dimension_pool"),
            before_metrics={
                "scenario_tags": list(candidate.get("scenario_tags") or []),
                "average_confidence": candidate.get("confidence"),
                "memory_match_score": candidate.get("memory_match_score"),
                "memory_support": dict(candidate.get("memory_support") or {}),
            },
            after_metrics={
                "methodology_tags": list(candidate.get("methodology_tags") or []),
                "aggregated_evidence_summary": evidence_summary_payload,
            },
            evidence_summary=str(evidence_summary_payload),
            related_entry_ids=[
                str(item)
                for item in (candidate.get("related_entry_ids") or [candidate.get("entry_id")])
                if str(item or "").strip()
            ],
        )


__all__ = ["ProposalGeneratorDimensionSupport"]
