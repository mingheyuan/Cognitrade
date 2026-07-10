from __future__ import annotations

from typing import Any


class MethodologyEvolverDimensionCandidates:
    def __init__(self, *, memory_support) -> None:
        self.memory_support = memory_support

    def build_candidates(
        self,
        *,
        run_id: str,
        entries: list[dict],
        memory_rows: list[dict[str, Any]],
        memory_search_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for artifact in entries:
            content = artifact.get("content") or {}
            candidates.append(
                {
                    "candidate_type": "dimension",
                    "run_id": run_id,
                    "artifact_id": artifact.get("artifact_id"),
                    "title": str(content.get("canonical_dimension_name") or content.get("dimension_name") or "unknown_dimension"),
                    "dimension_family": content.get("dimension_family"),
                    "scenario_tags": list(content.get("scenario_tags") or []),
                    "methodology_tags": list(content.get("methodology_tags") or []),
                    "confidence": artifact.get("confidence", content.get("confidence")),
                    "evidence_summary": {
                        "supported_claim_block_ids": list(content.get("supported_claim_block_ids") or []),
                        "evidence_block_ids": list(content.get("evidence_block_ids") or []),
                    },
                    "memory_context": self.memory_support.matching_memory_context(
                        memory_rows=memory_rows,
                        memory_search_rows=memory_search_rows,
                        title=str(content.get("canonical_dimension_name") or content.get("dimension_name") or "unknown_dimension"),
                        dimension_family=str(content.get("dimension_family") or "").strip() or None,
                    ),
                }
            )
        return candidates


__all__ = ["MethodologyEvolverDimensionCandidates"]
