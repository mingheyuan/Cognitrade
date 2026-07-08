from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.core.execution_policy import RuntimeDegradeReason
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.subagents.base import SubagentCatalogMetadata, SubagentRuntimePolicy


def _normalize_evidence_rows(evidences: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in evidences:
        if isinstance(item, BaseModel):
            normalized.append(item.model_dump(mode="python"))
        else:
            normalized.append(dict(item))
    return normalized


class RetrievalEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    evidence_id: str
    title: str
    summary: str
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    retrieval_hint: dict[str, Any] | None = None
    score: float | None = None


class BlackboardEvidenceIndexerInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blackboard_entries: list[dict[str, Any]] = Field(default_factory=list)


class BlackboardEvidenceIndexerOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_map: dict[str, RetrievalEvidenceModel] = Field(default_factory=dict)
    symbol: str | None = None
    sector: str | None = None
    current_run_id: str | None = None


class RetrievalDecisionInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    query: str
    evidences: list[RetrievalEvidenceModel] = Field(default_factory=list)
    top_k: int
    qdrant_degraded_reason: str | None = None
    planning_context_hints: dict[str, str] | None = None


class FallbackRetrievalDecisionInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    query: str
    evidences: list[RetrievalEvidenceModel] = Field(default_factory=list)
    top_k: int
    qdrant_degraded_reason: str | None = None
    planning_context_hints: dict[str, str] | None = None
    runtime_degraded_reason: str | None = None
    error_name: str | None = None


class RetrievalDecisionOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    query: str
    evidences: list[RetrievalEvidenceModel] = Field(default_factory=list)
    degraded: bool = False
    degrade_reason: str | None = None
    retrieval_summary: str


class QdrantEvidenceSearchInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    query: str
    top_k: int
    enabled: bool = True
    disabled_reason: str | None = None
    symbol: str | None = None
    sector: str | None = None
    current_run_id: str | None = None


class QdrantEvidenceSearchOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidences: list[RetrievalEvidenceModel] = Field(default_factory=list)
    metadata_filters: dict[str, str] = Field(default_factory=dict)
    hit_count: int = 0
    degraded: bool = False
    degrade_reason: str | None = None


class MethodologyMemoryRowModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    memory_type: str | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    match_score: float | None = None


class MethodologyMemoryRecallInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    query: str | None = None
    planning_context_hints: dict[str, str] | None = None
    limit: int = 5


class MethodologyMemoryRecallOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_rows: list[MethodologyMemoryRowModel] = Field(default_factory=list)
    search_query: str | None = None
    benchmark_scope: str | None = None
    degraded: bool = False
    degrade_reason: str | None = None


class EvidenceCandidateDeduperInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidences: list[RetrievalEvidenceModel] = Field(default_factory=list)
    current_run_id: str | None = None
    intent: str | None = None


class EvidenceCandidateDeduperOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidences: list[RetrievalEvidenceModel] = Field(default_factory=list)


class BlackboardEvidenceIndexerSubagent:
    subagent_type = "blackboard_evidence_indexer"
    input_model = BlackboardEvidenceIndexerInputModel
    output_model = BlackboardEvidenceIndexerOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Index blackboard entries into structured evidence candidates for downstream retrieval.",
        capabilities=("evidence_indexing", "blackboard_projection"),
        stage="retrieve",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("stock_check", "stock_compare", "stock_screen", "sector_research", "distill_kol_logic", "review_thesis", "methodology_evolution"),
        tags=("deterministic", "preprocess"),
    )

    def __init__(self, *, index_blackboard_evidences: Callable[..., dict[str, Any]]) -> None:
        self.index_blackboard_evidences = index_blackboard_evidences

    def run(self, *, blackboard_entries: list[dict[str, Any]]) -> dict[str, Any]:
        outputs = self.index_blackboard_evidences(blackboard_entries=blackboard_entries)
        evidence_map = {
            str(evidence_id): RetrievalEvidenceModel.model_validate(payload).model_dump(mode="python")
            for evidence_id, payload in dict(outputs.get("evidence_map") or {}).items()
        }
        return {
            "evidence_map": evidence_map,
            "symbol": outputs.get("symbol"),
            "sector": outputs.get("sector"),
            "current_run_id": outputs.get("current_run_id"),
        }


class QdrantEvidenceSearchSubagent:
    subagent_type = "qdrant_evidence_searcher"
    input_model = QdrantEvidenceSearchInputModel
    output_model = QdrantEvidenceSearchOutputModel
    runtime_policy = SubagentRuntimePolicy(allow_parallel=True, max_concurrency=2)
    catalog_metadata = SubagentCatalogMetadata(
        summary="Search indexed evidence in Qdrant using the current retrieval intent and filters.",
        capabilities=("vector_search", "evidence_recall"),
        stage="retrieve",
        cost_class="medium",
        latency_class="medium",
        side_effect_level="read_only",
        suitable_intents=("stock_check", "stock_compare", "sector_research", "review_thesis", "methodology_evolution"),
        tags=("parallel_fanout", "read_only"),
    )

    def __init__(self, *, search_qdrant_evidences: Callable[..., dict[str, Any]]) -> None:
        self.search_qdrant_evidences = search_qdrant_evidences

    def run(
        self,
        *,
        intent: str,
        query: str,
        top_k: int,
        enabled: bool = True,
        disabled_reason: str | None = None,
        symbol: str | None = None,
        sector: str | None = None,
        current_run_id: str | None = None,
    ) -> dict[str, Any]:
        if not enabled:
            return {
                "evidences": [],
                "metadata_filters": {},
                "hit_count": 0,
                "degraded": True,
                "degrade_reason": disabled_reason or RuntimeDegradeReason.RUNTIME_FALLBACK.value,
            }
        try:
            outputs = self.search_qdrant_evidences(
                intent=intent,
                query=query,
                top_k=top_k,
                symbol=symbol,
                sector=sector,
                current_run_id=current_run_id,
            )
        except Exception as exc:
            return {
                "evidences": [],
                "metadata_filters": {},
                "hit_count": 0,
                "degraded": True,
                "degrade_reason": f"qdrant_search_error:{type(exc).__name__}",
            }
        return {
            "evidences": _normalize_evidence_rows(outputs.get("evidences") or []),
            "metadata_filters": dict(outputs.get("metadata_filters") or {}),
            "hit_count": int(outputs.get("hit_count") or 0),
            "degraded": bool(outputs.get("degraded")),
            "degrade_reason": outputs.get("degrade_reason"),
        }


class MethodologyMemoryRecallSubagent:
    subagent_type = "methodology_memory_recaller"
    input_model = MethodologyMemoryRecallInputModel
    output_model = MethodologyMemoryRecallOutputModel
    runtime_policy = SubagentRuntimePolicy(allow_parallel=True, max_concurrency=2)
    catalog_metadata = SubagentCatalogMetadata(
        summary="Recall methodology memory rows to bias evidence ranking and summarization.",
        capabilities=("memory_recall", "methodology_context"),
        stage="retrieve",
        cost_class="medium",
        latency_class="medium",
        side_effect_level="read_only",
        suitable_intents=("stock_check", "stock_compare", "stock_screen", "review_thesis"),
        tags=("parallel_fanout", "memory"),
    )

    def __init__(self, *, recall_methodology_memory: Callable[..., dict[str, Any]]) -> None:
        self.recall_methodology_memory = recall_methodology_memory

    def run(
        self,
        *,
        intent: str,
        query: str | None = None,
        planning_context_hints: dict[str, str] | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        try:
            outputs = self.recall_methodology_memory(
                intent=intent,
                query=query,
                planning_context_hints=planning_context_hints,
                limit=limit,
            )
        except Exception as exc:
            return {
                "memory_rows": [],
                "search_query": None,
                "benchmark_scope": None,
                "degraded": True,
                "degrade_reason": f"methodology_memory_error:{type(exc).__name__}",
            }
        rows = outputs.get("memory_rows") or []
        normalized_rows = [
            MethodologyMemoryRowModel.model_validate(row).model_dump(mode="python")
            for row in rows
        ]
        return {
            "memory_rows": normalized_rows,
            "search_query": outputs.get("search_query"),
            "benchmark_scope": outputs.get("benchmark_scope"),
            "degraded": bool(outputs.get("degraded")),
            "degrade_reason": outputs.get("degrade_reason"),
        }


class EvidenceCandidateDeduperSubagent:
    subagent_type = "evidence_candidate_deduper"
    input_model = EvidenceCandidateDeduperInputModel
    output_model = EvidenceCandidateDeduperOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Deduplicate and normalize evidence candidates before final retrieval selection.",
        capabilities=("evidence_deduplication", "evidence_normalization"),
        stage="retrieve",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("stock_check", "stock_compare", "stock_screen", "sector_research", "distill_kol_logic", "review_thesis", "methodology_evolution"),
        tags=("deterministic",),
    )

    def __init__(self, *, dedupe_evidence_candidates: Callable[..., dict[str, Any]]) -> None:
        self.dedupe_evidence_candidates = dedupe_evidence_candidates

    def run(
        self,
        *,
        evidences: list[dict[str, Any]],
        current_run_id: str | None = None,
        intent: str | None = None,
    ) -> dict[str, Any]:
        outputs = self.dedupe_evidence_candidates(
            evidences=_normalize_evidence_rows(evidences),
            current_run_id=current_run_id,
            intent=intent,
        )
        return {
            "evidences": _normalize_evidence_rows(outputs.get("evidences") or []),
        }


class RetrievalDecisionSubagent:
    subagent_type = "retrieval_decider"
    input_model = RetrievalDecisionInputModel
    output_model = RetrievalDecisionOutputModel
    runtime_policy = SubagentRuntimePolicy(
        requires_llm=True,
        fallback_subagent_type="fallback_retrieval_decider",
        error_fallback_subagent_type="fallback_retrieval_decider",
    )
    catalog_metadata = SubagentCatalogMetadata(
        summary="Use an LLM to select the best evidence set when deterministic retrieval is insufficient.",
        capabilities=("evidence_selection", "llm_reasoning"),
        stage="retrieve",
        cost_class="high",
        latency_class="high",
        side_effect_level="none",
        suitable_intents=("sector_research",),
        tags=("llm", "selector"),
    )

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
        evidences = _normalize_evidence_rows(evidences)
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
    input_model = FallbackRetrievalDecisionInputModel
    output_model = RetrievalDecisionOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Fallback retrieval selector that deterministically chooses evidence when LLM retrieval is unavailable.",
        capabilities=("evidence_selection", "fallback_retrieval"),
        stage="retrieve",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("stock_check", "stock_compare", "stock_screen", "sector_research", "distill_kol_logic", "review_thesis", "methodology_evolution"),
        tags=("deterministic", "fallback"),
    )

    def __init__(
        self,
        *,
        combine_degrade_reason: Callable[[str | None, str | None], str | None],
        fallback_summary: Callable[..., str],
        select_compare_evidences: Callable[..., list[dict]],
        select_screening_evidences: Callable[..., list[dict]],
        select_stock_check_evidences: Callable[..., list[dict]],
        select_methodology_evolution_evidences: Callable[..., list[dict]],
        select_distillation_evidences: Callable[..., list[dict]],
        select_review_evidences: Callable[..., list[dict]],
    ) -> None:
        self.combine_degrade_reason = combine_degrade_reason
        self.fallback_summary = fallback_summary
        self.select_compare_evidences = select_compare_evidences
        self.select_screening_evidences = select_screening_evidences
        self.select_stock_check_evidences = select_stock_check_evidences
        self.select_methodology_evolution_evidences = select_methodology_evolution_evidences
        self.select_distillation_evidences = select_distillation_evidences
        self.select_review_evidences = select_review_evidences

    def run(
        self,
        *,
        intent: str,
        query: str,
        evidences: list[dict],
        top_k: int,
        qdrant_degraded_reason: str | None,
        planning_context_hints: dict[str, str] | None = None,
        runtime_degraded_reason: str | None = None,
        error_name: str | None = None,
    ) -> dict:
        evidences = _normalize_evidence_rows(evidences)
        if intent == "distill_kol_logic":
            fallback_evidences = self.select_distillation_evidences(
                evidences=evidences,
                top_k=top_k,
            )
        elif intent == "stock_screen":
            fallback_evidences = self.select_screening_evidences(
                evidences=evidences,
                top_k=top_k,
                query=query,
                planning_context_hints=planning_context_hints or {},
            )
        elif intent == "stock_compare":
            fallback_evidences = self.select_compare_evidences(
                evidences=evidences,
                top_k=top_k,
                query=query,
                planning_context_hints=planning_context_hints or {},
            )
        elif intent == "stock_check":
            fallback_evidences = self.select_stock_check_evidences(
                evidences=evidences,
                top_k=top_k,
                query=query,
                planning_context_hints=planning_context_hints or {},
            )
        elif intent == "review_thesis":
            fallback_evidences = self.select_review_evidences(
                evidences=evidences,
                top_k=top_k,
                query=query,
                planning_context_hints=planning_context_hints or {},
            )
        elif intent == "methodology_evolution":
            fallback_evidences = self.select_methodology_evolution_evidences(
                evidences=evidences,
                top_k=top_k,
            )
        else:
            fallback_evidences = evidences[:top_k]
        base_reason = runtime_degraded_reason or (
            f"llm_error:{error_name}" if error_name else RuntimeDegradeReason.LLM_UNAVAILABLE.value
        )
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
