from __future__ import annotations


class ProposalPayloadFields:
    def proposal_detail_fields(self, payload: dict) -> list[tuple[str, str]]:
        fields: list[tuple[str, str]] = []
        ordered = (
            ("proposal_id", payload.get("proposal_id")),
            ("status", payload.get("status") or "unknown"),
            ("proposal_type", payload.get("proposal_type") or "unknown"),
            ("title", payload.get("title")),
            ("summary", payload.get("summary")),
            ("sample_size", payload.get("sample_size")),
            ("benchmark_scope", payload.get("benchmark_scope")),
            ("review_reason", payload.get("review_reason")),
            ("reviewer", payload.get("reviewer")),
            ("reviewed_at", payload.get("reviewed_at")),
            ("created_from_run_id", payload.get("created_from_run_id")),
        )
        for label, value in ordered:
            if value in (None, ""):
                continue
            fields.append((label, str(value)))
        return fields

    def proposal_result_sections(self, payload: dict) -> list[dict]:
        result = payload.get("result")
        if not isinstance(result, dict):
            return []
        sections: list[dict] = []
        for key in ("manifest", "retrieval_policy", "memory_record"):
            value = result.get(key)
            if value is not None:
                sections.append({key: value})
        return sections


__all__ = ["ProposalPayloadFields"]
