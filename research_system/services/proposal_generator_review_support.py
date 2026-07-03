from __future__ import annotations

from typing import Any

from research_system.schemas.proposals import ProposalDraft


class ProposalGeneratorReviewSupport:
    def build(self, *, author_agent: str, candidate: dict[str, Any]) -> ProposalDraft:
        sample_size = int(candidate.get("sample_size") or 0)
        severity_counts = dict(candidate.get("severity_counts") or {})
        verdict_counts = dict(candidate.get("verdict_counts") or {})
        return ProposalDraft(
            proposal_type="MemoryLearningUpdate",
            title="Capture review failure pattern",
            summary="Persist recurring review severity/verdict patterns into long-term methodology memory.",
            created_from_run_id=str(candidate.get("run_id") or ""),
            author_agent=author_agent,
            sample_size=max(sample_size, 1),
            benchmark_scope="review_pattern",
            before_metrics={"severity_counts": severity_counts},
            after_metrics={"verdict_counts": verdict_counts},
            evidence_summary=str(
                {
                    "sample_size": sample_size,
                    "related_run_ids": list(candidate.get("related_run_ids") or []),
                    "severity_counts": severity_counts,
                    "verdict_counts": verdict_counts,
                }
            ),
            related_entry_ids=[str(item) for item in (candidate.get("related_entry_ids") or []) if str(item or "").strip()],
        )


__all__ = ["ProposalGeneratorReviewSupport"]
