from __future__ import annotations

from typing import Any, Callable

from research_system.runtime.providers.provider_registry import OpenAIProvider


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
        select_compare_evidences: Callable[..., list[dict]],
        select_screening_evidences: Callable[..., list[dict]],
        select_stock_check_evidences: Callable[..., list[dict]],
        select_methodology_evolution_evidences: Callable[..., list[dict]],
        select_distillation_evidences: Callable[..., list[dict]],
        select_review_evidences: Callable[..., list[dict]],
        retrieval_decision_schema: Any,
    ) -> None:
        self.llm_provider = llm_provider
        self.structured_retrieval_summary = structured_retrieval_summary
        self.merge_retrieval_summary = merge_retrieval_summary
        self.combine_degrade_reason = combine_degrade_reason
        self.fallback_summary = fallback_summary
        self.select_compare_evidences = select_compare_evidences
        self.select_screening_evidences = select_screening_evidences
        self.select_stock_check_evidences = select_stock_check_evidences
        self.select_methodology_evolution_evidences = select_methodology_evolution_evidences
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
        planning_context_hints: dict[str, str] | None = None,
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
            selected = self.select_screening_evidences(
                evidences=evidences,
                top_k=top_k,
                query=query,
                planning_context_hints=planning_context_hints or {},
            )
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
        if intent == "stock_compare":
            selected = self.select_compare_evidences(
                evidences=evidences,
                top_k=top_k,
                query=query,
                planning_context_hints=planning_context_hints or {},
            )
            return {
                "intent": intent,
                "query": query,
                "evidences": selected,
                "degraded": bool(qdrant_degraded_reason),
                "degrade_reason": qdrant_degraded_reason,
                "retrieval_summary": self.merge_retrieval_summary(
                    self.structured_retrieval_summary(intent=intent, evidences=selected),
                    "Deterministic stock comparison retrieval completed.",
                ),
            }
        if intent == "stock_check":
            selected = self.select_stock_check_evidences(
                evidences=evidences,
                top_k=top_k,
                query=query,
                planning_context_hints=planning_context_hints or {},
            )
            return {
                "intent": intent,
                "query": query,
                "evidences": selected,
                "degraded": bool(qdrant_degraded_reason),
                "degrade_reason": qdrant_degraded_reason,
                "retrieval_summary": self.merge_retrieval_summary(
                    self.structured_retrieval_summary(intent=intent, evidences=selected),
                    "Deterministic stock check retrieval completed.",
                ),
            }
        if intent == "review_thesis":
            selected = self.select_review_evidences(
                evidences=evidences,
                top_k=top_k,
                query=query,
                planning_context_hints=planning_context_hints or {},
            )
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
        if intent == "methodology_evolution":
            selected = self.select_methodology_evolution_evidences(evidences=evidences, top_k=top_k)
            return {
                "intent": intent,
                "query": query,
                "evidences": selected,
                "degraded": bool(qdrant_degraded_reason),
                "degrade_reason": qdrant_degraded_reason,
                "retrieval_summary": self.merge_retrieval_summary(
                    self.structured_retrieval_summary(intent=intent, evidences=selected),
                    "Deterministic methodology evolution retrieval completed.",
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


class FallbackRetrievalDecisionSubagent:
    subagent_type = "fallback_retrieval_decider"

    def __init__(
        self,
        *,
        combine_degrade_reason: Callable[[str | None, str | None], str | None],
        fallback_summary: Callable[..., str],
    ) -> None:
        self.combine_degrade_reason = combine_degrade_reason
        self.fallback_summary = fallback_summary

    def run(
        self,
        *,
        intent: str,
        query: str,
        evidences: list[dict],
        top_k: int,
        qdrant_degraded_reason: str | None,
        runtime_degraded_reason: str | None = None,
        error_name: str | None = None,
    ) -> dict:
        fallback_evidences = evidences[:top_k]
        base_reason = runtime_degraded_reason or (f"llm_error:{error_name}" if error_name else "llm_unavailable")
        return {
            "intent": intent,
            "query": query,
            "evidences": fallback_evidences,
            "degraded": True,
            "degrade_reason": self.combine_degrade_reason(base_reason, qdrant_degraded_reason),
            "retrieval_summary": self.fallback_summary(
                intent=intent,
                evidences=fallback_evidences,
                qdrant_degraded_reason=qdrant_degraded_reason,
            ),
        }
