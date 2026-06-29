from __future__ import annotations

from typing import Any, Callable

from research_system.runtime.provider_registry import LLMProviderError, OpenAIProvider


class BlackboardEvidenceIndexerSubagent:
    subagent_type = "blackboard_evidence_indexer"

    def __init__(
        self,
        *,
        qdrant: Any,
        to_blackboard_entry: Callable[[dict], Any],
        evidence_from_blackboard_entry: Callable[[dict], dict],
    ) -> None:
        self.qdrant = qdrant
        self.to_blackboard_entry = to_blackboard_entry
        self.evidence_from_blackboard_entry = evidence_from_blackboard_entry

    def run(self, *, blackboard_entries: list[dict]) -> dict:
        evidence_map: dict[str, dict] = {}
        symbol = None
        sector = None
        current_run_id = blackboard_entries[0]["run_id"] if blackboard_entries else None
        for entry in blackboard_entries:
            if entry["kind"] in {"source_record", "verified_snapshot", "logic_block", "distilled_dimension", "review_finding"}:
                if self.qdrant is not None:
                    self.qdrant.upsert_entry(self.to_blackboard_entry(entry))
                evidence = self.evidence_from_blackboard_entry(entry)
                evidence_map[evidence["evidence_id"]] = evidence
            symbol = entry.get("payload", {}).get("symbol") or symbol
            sector = entry.get("payload", {}).get("sector") or sector
        return {
            "evidence_map": evidence_map,
            "symbol": symbol,
            "sector": sector,
            "current_run_id": current_run_id,
        }


class QdrantEvidenceSearcherSubagent:
    subagent_type = "qdrant_evidence_searcher"

    def __init__(self, *, qdrant: Any, retrieval_hint_from_qdrant_payload: Callable[[dict], dict | None]) -> None:
        self.qdrant = qdrant
        self.retrieval_hint_from_qdrant_payload = retrieval_hint_from_qdrant_payload

    def run(
        self,
        *,
        query: str,
        top_k: int,
        symbol: str | None,
        sector: str | None,
        current_run_id: str | None,
        evidence_map: dict[str, dict],
    ) -> dict:
        metadata_filters: dict[str, str] = {"record_type": "verified_snapshot"}
        if symbol:
            metadata_filters["symbol"] = symbol
        elif sector:
            metadata_filters["sector"] = sector
        elif current_run_id:
            metadata_filters["run_id"] = current_run_id

        qdrant_hits = self.qdrant.search(
            query_text=query,
            top_k=top_k,
            metadata_filters=metadata_filters,
        )
        for hit in qdrant_hits:
            payload = hit["payload"]
            evidence_id = payload["entry_id"]
            candidate = {
                "evidence_id": evidence_id,
                "title": payload["document_title"],
                "summary": payload["content"],
                "retrieval_hint": self.retrieval_hint_from_qdrant_payload(payload),
                "source_refs": payload.get("source_refs", []),
                "metadata": {
                    "phase": payload.get("phase"),
                    "agent": payload.get("agent"),
                    "record_type": payload.get("record_type"),
                    "source_type": payload.get("source_type"),
                },
                "score": hit["score"],
            }
            existing = evidence_map.get(evidence_id)
            if existing is None or (existing.get("score") or 0.0) < (candidate.get("score") or 0.0):
                merged = dict(existing or {})
                merged.update(candidate)
                evidence_map[evidence_id] = merged
        return {
            "evidence_map": evidence_map,
            "metadata_filters": metadata_filters,
        }


class EvidenceDeduperSubagent:
    subagent_type = "evidence_deduper"

    def __init__(self, *, dedupe_evidences: Callable[..., list[dict]]) -> None:
        self.dedupe_evidences = dedupe_evidences

    def run(self, *, evidences: list[dict], current_run_id: str | None) -> dict:
        deduped = self.dedupe_evidences(evidences=evidences, current_run_id=current_run_id)
        deduped.sort(key=lambda item: item.get("score") or 0.0, reverse=True)
        return {
            "evidences": deduped,
        }


class RetrievalDecisionSubagent:
    subagent_type = "retrieval_decider"

    def __init__(
        self,
        *,
        llm_provider: OpenAIProvider,
        structured_retrieval_summary: Callable[..., str | None],
        merge_retrieval_summary: Callable[[str | None, str | None], str],
        combine_degrade_reason: Callable[[str | None, str | None], str | None],
        fallback_summary: Callable[..., str],
        select_screening_evidences: Callable[..., list[dict]],
        select_distillation_evidences: Callable[..., list[dict]],
        select_review_evidences: Callable[..., list[dict]],
        retrieval_decision_schema: Any,
    ) -> None:
        self.llm_provider = llm_provider
        self.structured_retrieval_summary = structured_retrieval_summary
        self.merge_retrieval_summary = merge_retrieval_summary
        self.combine_degrade_reason = combine_degrade_reason
        self.fallback_summary = fallback_summary
        self.select_screening_evidences = select_screening_evidences
        self.select_distillation_evidences = select_distillation_evidences
        self.select_review_evidences = select_review_evidences
        self.retrieval_decision_schema = retrieval_decision_schema

    def run(
        self,
        *,
        intent: str,
        query: str,
        evidences: list[dict],
        top_k: int,
        qdrant_degraded_reason: str | None,
    ) -> dict:
        if intent == "distill_kol_logic":
            selected = self.select_distillation_evidences(evidences=evidences, top_k=top_k)
            return {
                "intent": intent,
                "query": query,
                "evidences": selected,
                "degraded": bool(qdrant_degraded_reason),
                "degrade_reason": qdrant_degraded_reason,
                "retrieval_summary": self.merge_retrieval_summary(
                    self.structured_retrieval_summary(intent=intent, evidences=selected),
                    "Deterministic distillation retrieval completed.",
                ),
            }
        if intent == "stock_screen":
            selected = self.select_screening_evidences(evidences=evidences, top_k=top_k)
            return {
                "intent": intent,
                "query": query,
                "evidences": selected,
                "degraded": bool(qdrant_degraded_reason),
                "degrade_reason": qdrant_degraded_reason,
                "retrieval_summary": self.merge_retrieval_summary(
                    self.structured_retrieval_summary(intent=intent, evidences=selected),
                    "Deterministic stock screening retrieval completed.",
                ),
            }
        if intent == "review_thesis":
            selected = self.select_review_evidences(evidences=evidences, top_k=top_k)
            return {
                "intent": intent,
                "query": query,
                "evidences": selected,
                "degraded": bool(qdrant_degraded_reason),
                "degrade_reason": qdrant_degraded_reason,
                "retrieval_summary": self.merge_retrieval_summary(
                    self.structured_retrieval_summary(intent=intent, evidences=selected),
                    "Deterministic review retrieval completed.",
                ),
            }

        if not evidences:
            return {
                "intent": intent,
                "query": query,
                "evidences": [],
                "degraded": True,
                "retrieval_summary": "No evidence candidates available.",
                "degrade_reason": "missing_candidates",
            }

        if not self.llm_provider.available:
            fallback_evidences = evidences[:top_k]
            return {
                "intent": intent,
                "query": query,
                "evidences": fallback_evidences,
                "degraded": True,
                "degrade_reason": self.combine_degrade_reason("llm_unavailable", qdrant_degraded_reason),
                "retrieval_summary": self.fallback_summary(
                    intent=intent,
                    evidences=fallback_evidences,
                    qdrant_degraded_reason=qdrant_degraded_reason,
                ),
            }

        evidence_block = [
            {
                "evidence_id": item["evidence_id"],
                "title": item["title"],
                "summary": item["summary"],
                "metadata": item["metadata"],
                "source_refs": item["source_refs"],
            }
            for item in evidences
        ]

        try:
            decision = self.llm_provider.parse(
                system_prompt=(
                    "You are a retrieval agent for an A-share research system.\n"
                    "Select the most relevant evidence candidates for the user's research intent.\n"
                    "Do not invent evidence. Prefer verified snapshots when exact claims may matter."
                ),
                user_prompt=(
                    f"Intent: {intent}\n"
                    f"User query: {query}\n"
                    f"Top K limit: {top_k}\n"
                    f"Evidence candidates: {evidence_block}\n\n"
                    "Return a JSON object with keys "
                    "`selected_evidence_ids`, `retrieval_summary`, `degraded`, and `degrade_reason`.\n"
                    "Use `selected_evidence_ids` for the chosen evidence ids."
                ),
                schema=self.retrieval_decision_schema,
                temperature=0.0,
                reasoning_effort="low",
            )
        except LLMProviderError as exc:
            fallback_evidences = evidences[:top_k]
            return {
                "intent": intent,
                "query": query,
                "evidences": fallback_evidences,
                "degraded": True,
                "degrade_reason": self.combine_degrade_reason(f"llm_error:{exc.__class__.__name__}", qdrant_degraded_reason),
                "retrieval_summary": self.fallback_summary(
                    intent=intent,
                    evidences=fallback_evidences,
                    qdrant_degraded_reason=qdrant_degraded_reason,
                ),
            }

        selected_ids = decision.selected_evidence_ids[:top_k]
        if not selected_ids:
            selected_ids = [item["evidence_id"] for item in evidences[:top_k]]
            decision.degraded = True
            decision.degrade_reason = "empty_selection"

        selected_lookup = {item["evidence_id"]: item for item in evidences}
        selected = [selected_lookup[evidence_id] for evidence_id in selected_ids if evidence_id in selected_lookup]
        if not selected:
            selected = evidences[:top_k]
            decision.degraded = True
            decision.degrade_reason = "selection_mismatch"

        structured_summary = self.structured_retrieval_summary(intent=intent, evidences=selected)
        return {
            "intent": intent,
            "query": query,
            "evidences": selected,
            "degraded": decision.degraded or bool(qdrant_degraded_reason),
            "degrade_reason": self.combine_degrade_reason(decision.degrade_reason, qdrant_degraded_reason),
            "retrieval_summary": self.merge_retrieval_summary(structured_summary, decision.retrieval_summary),
        }
