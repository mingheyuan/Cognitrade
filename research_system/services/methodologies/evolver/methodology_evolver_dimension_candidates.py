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
        for entry in entries:
            payload = entry.get("payload") or {}
            candidates.append(
                {
                    "candidate_type": "dimension",
                    "run_id": run_id,
                    "entry_id": entry.get("entry_id"),
                    "title": str(payload.get("canonical_dimension_name") or payload.get("dimension_name") or "unknown_dimension"),
                    "dimension_family": payload.get("dimension_family"),
                    "scenario_tags": list(payload.get("scenario_tags") or []),
                    "methodology_tags": list(payload.get("methodology_tags") or []),
                    "confidence": payload.get("confidence"),
                    "evidence_summary": {
                        "supported_claim_block_ids": list(payload.get("supported_claim_block_ids") or []),
                        "evidence_block_ids": list(payload.get("evidence_block_ids") or []),
                    },
                    "memory_context": self.memory_support.matching_memory_context(
                        memory_rows=memory_rows,
                        memory_search_rows=memory_search_rows,
                        title=str(payload.get("canonical_dimension_name") or payload.get("dimension_name") or "unknown_dimension"),
                        dimension_family=str(payload.get("dimension_family") or "").strip() or None,
                    ),
                }
            )
        return candidates


__all__ = ["MethodologyEvolverDimensionCandidates"]
