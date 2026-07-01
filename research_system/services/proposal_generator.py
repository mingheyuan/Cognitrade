from __future__ import annotations

from typing import Any

from research_system.schemas.proposals import ProposalDraft


class ProposalGenerator:
    def build(self, *, author_agent: str, candidates: list[dict[str, Any]]) -> list[ProposalDraft]:
        proposals: list[ProposalDraft] = []
        for candidate in candidates:
            candidate_type = str(candidate.get("candidate_type") or "")
            if candidate_type == "dimension":
                title = str(candidate.get("title") or "dimension_candidate")
                evidence_summary_payload = candidate.get("aggregated_evidence_summary") or candidate.get("evidence_summary") or {}
                sample_size = int(candidate.get("sample_size") or max(len(candidate.get("related_run_ids") or []), 0) or max(len((candidate.get("evidence_summary") or {}).get("supported_claim_block_ids") or []), 1))
                proposals.append(
                    ProposalDraft(
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
                        related_entry_ids=[str(item) for item in (candidate.get("related_entry_ids") or [candidate.get("entry_id")]) if str(item or "").strip()],
                    )
                )
            elif candidate_type == "valuation_heuristic":
                title = str(candidate.get("title") or "valuation_heuristic")
                evidence_summary_payload = candidate.get("aggregated_evidence_summary") or candidate.get("evidence_summary") or {}
                sample_size = int(candidate.get("sample_size") or max(len(candidate.get("related_run_ids") or []), 1))
                proposals.append(
                    ProposalDraft(
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
                        related_entry_ids=[str(item) for item in (candidate.get("related_entry_ids") or [candidate.get("entry_id")]) if str(item or "").strip()],
                    )
                )
            elif candidate_type == "review_pattern":
                sample_size = int(candidate.get("sample_size") or 0)
                severity_counts = dict(candidate.get("severity_counts") or {})
                verdict_counts = dict(candidate.get("verdict_counts") or {})
                proposals.append(
                    ProposalDraft(
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
                )
        return proposals
