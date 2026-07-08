from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from research_system.adapters.sources.xueqiu_statuses import load_statuses
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.providers.provider_registry import LLMProviderError, OpenAIProvider
from research_system.schemas.distillation import (
    DistillSourceRecord,
    DistillationResult,
    DistillationSummary,
    DistilledDimension,
    LogicBlock,
)
from research_system.subagents.base import SubagentCatalogMetadata, SubagentRuntimePolicy


class DistillSourceNormalizerInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    source_records: list[dict] = Field(default_factory=list)


class DistillSourceNormalizerOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[DistillSourceRecord] = Field(default_factory=list)


class DistillRecordTrimmerInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[DistillSourceRecord] = Field(default_factory=list)
    max_records: int = 6
    max_chars: int = 6000


class DistillRecordTrimmerOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[DistillSourceRecord] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DistillChunkBuilderInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[DistillSourceRecord] = Field(default_factory=list)
    max_chunk_records: int = 3
    max_chunk_chars: int = 2400


class DistillChunkBuilderOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunks: list[list[DistillSourceRecord]] = Field(default_factory=list)


class ChunkExecutionStrategyResolverInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    chunk_count: int
    force_fallback: bool = False
    runtime_degraded_reason: str | None = None


class ChunkExecutionStrategyResolverOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_strategy: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class LLMChunkDistillerInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    records: list[DistillSourceRecord] = Field(default_factory=list)


class FallbackChunkDistillerInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[DistillSourceRecord] = Field(default_factory=list)
    error_name: str | None = None


class DistillationEnvelopeOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_record_id: str
    summary: DistillationSummary
    logic_blocks: list[LogicBlock] = Field(default_factory=list)
    distilled_dimensions: list[DistilledDimension] = Field(default_factory=list)
    degraded: bool = False
    degrade_reason: str | None = None


class ChunkResultMergerInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[DistillSourceRecord] = Field(default_factory=list)
    chunk_results: list[DistillationEnvelopeOutputModel] = Field(default_factory=list)


class DistillSourceNormalizerSubagent:
    subagent_type = "distill_source_normalizer"
    input_model = DistillSourceNormalizerInputModel
    output_model = DistillSourceNormalizerOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Normalize distillation inputs and expand structured source file references into source records.",
        capabilities=("source_normalization", "source_record_loading"),
        stage="distill",
        cost_class="low",
        latency_class="low",
        side_effect_level="read_only",
        suitable_intents=("distill_kol_logic",),
        tags=("deterministic", "preprocess"),
    )

    def __init__(self, *, extract_source_path: Callable[[str], Path | None]) -> None:
        self.extract_source_path = extract_source_path

    def run(self, *, query: str, source_records: list[dict]) -> dict:
        normalized: list[DistillSourceRecord] = []
        loaded_paths: set[str] = set()
        explicit_path = self.extract_source_path(query)
        if explicit_path:
            normalized.extend(self._load_statuses_from_path(explicit_path))
            loaded_paths.add(str(explicit_path.resolve()))

        for source in source_records:
            content = str(source.get("content") or "").strip()
            title = str(source.get("title") or "").strip() or None
            source_type = str(source.get("source_type") or "user_query")
            source_id = str(source.get("source_id") or generate_ulid())
            if source_type in {"xueqiu_statuses_jsonl", "distill_request"}:
                path = source.get("path") or self.extract_source_path(content)
                if path:
                    resolved = str(Path(path).resolve())
                    if resolved not in loaded_paths:
                        normalized.extend(self._load_statuses_from_path(Path(path)))
                        loaded_paths.add(resolved)
                    if source_type == "distill_request":
                        continue
                if source_type == "xueqiu_statuses_jsonl":
                    continue
            if not content:
                continue
            normalized.append(
                DistillSourceRecord(
                    source_id=source_id,
                    title=title,
                    content=content,
                    source_type=source_type,
                    source_url=source.get("source_url"),
                    author=source.get("author"),
                    platform=source.get("platform"),
                    published_at=source.get("published_at"),
                    metadata=source.get("metadata") or {},
                )
            )

        deduped: dict[str, DistillSourceRecord] = {}
        for record in normalized:
            deduped.setdefault(record.source_id, record)
        return {
            "records": list(deduped.values()),
        }

    def _load_statuses_from_path(self, path: Path) -> list[DistillSourceRecord]:
        records: list[DistillSourceRecord] = []
        for row in load_statuses(path):
            content = str(row.get("text") or row.get("description") or "").strip()
            if not content:
                continue
            records.append(
                DistillSourceRecord(
                    source_id=str(row.get("id") or generate_ulid()),
                    title=str(row.get("title") or "").strip() or None,
                    content=content,
                    source_type="xueqiu_status",
                    source_url=row.get("url"),
                    author=row.get("user_name"),
                    platform=row.get("source"),
                    published_at=row.get("created_at"),
                    metadata={
                        "user_id": row.get("user_id"),
                        "reply_count": row.get("reply_count"),
                        "like_count": row.get("like_count"),
                        "fav_count": row.get("fav_count"),
                        "is_column": row.get("is_column"),
                    },
                )
            )
        return records


class DistillRecordTrimmerSubagent:
    subagent_type = "distill_record_trimmer"
    input_model = DistillRecordTrimmerInputModel
    output_model = DistillRecordTrimmerOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Trim ranked source records into a bounded distillation set before chunking.",
        capabilities=("record_trimming", "distillation_preparation"),
        stage="distill",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("distill_kol_logic",),
        tags=("deterministic", "preprocess"),
    )

    def __init__(self, *, record_rank: Callable[[DistillSourceRecord], float]) -> None:
        self.record_rank = record_rank

    def run(self, *, records: list[DistillSourceRecord], max_records: int = 6, max_chars: int = 6000) -> dict:
        ranked = sorted(records, key=self.record_rank, reverse=True)
        selected: list[DistillSourceRecord] = []
        total_chars = 0
        for record in ranked:
            content_length = len(record.content)
            if selected and (len(selected) >= max_records or total_chars + content_length > max_chars):
                continue
            selected.append(record)
            total_chars += content_length
            if len(selected) >= max_records or total_chars >= max_chars:
                break
        if not selected and ranked:
            selected.append(ranked[0])
            total_chars = len(ranked[0].content)
        notes = []
        if len(records) > len(selected):
            notes.append(f"trimmer_selected_{len(selected)}_of_{len(records)}_records")
        notes.append(f"trimmer_total_chars={total_chars}")
        return {
            "records": selected,
            "notes": notes,
        }


class DistillChunkBuilderSubagent:
    subagent_type = "distill_chunk_builder"
    input_model = DistillChunkBuilderInputModel
    output_model = DistillChunkBuilderOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Split trimmed distillation records into chunk-sized batches for parallel processing.",
        capabilities=("chunk_building", "parallel_chunk_preparation"),
        stage="distill",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("distill_kol_logic",),
        tags=("deterministic", "parallel_fanout"),
    )

    def run(self, *, records: list[DistillSourceRecord], max_chunk_records: int = 3, max_chunk_chars: int = 2400) -> dict:
        chunks: list[list[DistillSourceRecord]] = []
        current: list[DistillSourceRecord] = []
        current_chars = 0
        for record in records:
            content_length = len(record.content)
            if current and (len(current) >= max_chunk_records or current_chars + content_length > max_chunk_chars):
                chunks.append(current)
                current = []
                current_chars = 0
            current.append(record)
            current_chars += content_length
        if current:
            chunks.append(current)
        return {
            "chunks": chunks,
        }


class LLMChunkDistillerSubagent:
    subagent_type = "llm_chunk_distiller"
    input_model = LLMChunkDistillerInputModel
    output_model = DistillationEnvelopeOutputModel
    runtime_policy = SubagentRuntimePolicy(
        requires_llm=True,
        fallback_subagent_type="fallback_chunk_distiller",
        error_fallback_subagent_type="fallback_chunk_distiller",
        allow_parallel=True,
        max_concurrency=4,
    )
    catalog_metadata = SubagentCatalogMetadata(
        summary="Distill one chunk of source records into structured logic blocks and dimensions with an LLM.",
        capabilities=("chunk_distillation", "llm_distillation"),
        stage="distill",
        cost_class="high",
        latency_class="medium",
        side_effect_level="none",
        suitable_intents=("distill_kol_logic",),
        tags=("llm", "parallel_fanout"),
    )

    def __init__(self, *, llm_provider: OpenAIProvider, schema: type, distillation_model: type) -> None:
        self.llm_provider = llm_provider
        self.schema = schema
        self.distillation_model = distillation_model

    def run(self, *, query: str, records: list[DistillSourceRecord]) -> dict:
        record_block = [record.model_dump(mode="json") for record in records[:5]]
        payload = self.llm_provider.parse(
            system_prompt=(
                "You are a structured distiller for A-share or market-adjacent KOL text.\n"
                "Extract reviewable logic blocks and dimensions.\n"
                "Do not invent symbols, sectors, time horizons, or causality.\n"
                "Separate author viewpoint from quoted content.\n"
                "When uncertain, lower confidence and mark needs_review or risk flags."
            ),
            user_prompt=(
                f"User query: {query}\n"
                f"Source records: {record_block}\n\n"
                "Return a JSON object aligned to DistillationResultPayload.\n"
                "Logic blocks must use block_type in claim/evidence/risk/catalyst.\n"
                "Set speaker_type to author, quoted, or uncertain.\n"
                "Distilled dimensions must include dimension_name, dimension_category, related block ids, and confidence."
            ),
            schema=self.schema,
            temperature=0.1,
            reasoning_effort="medium",
        )
        return self.distillation_model.model_validate(payload.model_dump(mode="json")).model_dump(mode="json")


class ChunkExecutionStrategyResolverSubagent:
    subagent_type = "chunk_execution_strategy_resolver"
    input_model = ChunkExecutionStrategyResolverInputModel
    output_model = ChunkExecutionStrategyResolverOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Resolve whether chunk distillation should use the LLM branch or deterministic fallback branch.",
        capabilities=("chunk_execution_strategy", "distillation_planning"),
        stage="distill",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("distill_kol_logic",),
        tags=("deterministic", "strategy"),
    )

    def __init__(self, *, derive_chunk_execution_strategy: Callable[..., dict[str, Any]]) -> None:
        self.derive_chunk_execution_strategy = derive_chunk_execution_strategy

    def run(
        self,
        *,
        query: str,
        chunk_count: int,
        force_fallback: bool = False,
        runtime_degraded_reason: str | None = None,
    ) -> dict[str, Any]:
        execution_strategy = self.derive_chunk_execution_strategy(
            query=query,
            chunk_count=chunk_count,
            force_fallback=force_fallback,
            runtime_degraded_reason=runtime_degraded_reason,
        )
        notes = [f"chunk_count={chunk_count}"]
        if force_fallback:
            notes.append("force_fallback_requested")
        selected_subagent_type = str(execution_strategy.get("chunk_subagent_type") or "").strip()
        if selected_subagent_type:
            notes.append(f"chunk_subagent_type={selected_subagent_type}")
        return {
            "execution_strategy": execution_strategy,
            "notes": notes,
        }


class FallbackChunkDistillerSubagent:
    subagent_type = "fallback_chunk_distiller"
    input_model = FallbackChunkDistillerInputModel
    output_model = DistillationEnvelopeOutputModel
    runtime_policy = SubagentRuntimePolicy(allow_parallel=True, max_concurrency=4)
    catalog_metadata = SubagentCatalogMetadata(
        summary="Fallback chunk distillation using deterministic heuristics when LLM distillation is unavailable.",
        capabilities=("chunk_distillation", "fallback_distillation"),
        stage="distill",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("distill_kol_logic",),
        tags=("deterministic", "parallel_fanout", "fallback"),
    )

    def __init__(
        self,
        *,
        infer_block_type: Callable[[str], str],
        infer_speaker_type: Callable[[str], str],
        infer_attribution_label: Callable[[str], str | None],
        extract_symbols: Callable[[str], list[str]],
        extract_sectors: Callable[[str], list[str]],
        infer_time_horizon: Callable[[str], str | None],
        infer_direction: Callable[[str], str | None],
        infer_risk_flags: Callable[[str], list[str]],
        extract_dimensions: Callable[[str], list[tuple[str, str]]],
        dedupe_logic_blocks: Callable[[list[LogicBlock]], list[LogicBlock]],
        merge_dimensions: Callable[[list[DistilledDimension]], list[DistilledDimension]],
        distillation_result_model: type,
    ) -> None:
        self.infer_block_type = infer_block_type
        self.infer_speaker_type = infer_speaker_type
        self.infer_attribution_label = infer_attribution_label
        self.extract_symbols = extract_symbols
        self.extract_sectors = extract_sectors
        self.infer_time_horizon = infer_time_horizon
        self.infer_direction = infer_direction
        self.infer_risk_flags = infer_risk_flags
        self.extract_dimensions = extract_dimensions
        self.dedupe_logic_blocks = dedupe_logic_blocks
        self.merge_dimensions = merge_dimensions
        self.distillation_result_model = distillation_result_model

    def run(
        self,
        *,
        records: list[DistillSourceRecord],
        error_name: str | None = None,
    ) -> dict:
        logic_blocks: list[LogicBlock] = []
        dimensions: dict[str, DistilledDimension] = {}
        for record in records[:10]:
            lines = [line.strip() for line in record.content.splitlines() if line.strip()]
            if not lines:
                continue
            for index, line in enumerate(lines[:4], start=1):
                block_type = self.infer_block_type(line)
                block = LogicBlock(
                    block_type=block_type,
                    source_record_id=record.source_id,
                    source_span={"start_line": index, "end_line": index, "excerpt": line[:240]},
                    speaker_type=self.infer_speaker_type(line),
                    attribution_label=self.infer_attribution_label(line),
                    text=line[:500],
                    normalized_claim=line[:200] if block_type == "claim" else None,
                    symbols=self.extract_symbols(line),
                    sectors=self.extract_sectors(line),
                    time_horizon=self.infer_time_horizon(line),
                    direction=self.infer_direction(line),
                    evidence_refs=[{"source_record_id": record.source_id, "line": index}],
                    risk_flags=self.infer_risk_flags(line),
                    confidence=0.55 if block_type == "claim" else 0.6,
                )
                logic_blocks.append(block)
                for dimension_name, category in self.extract_dimensions(line):
                    existing = dimensions.get(dimension_name)
                    if existing is None:
                        existing = DistilledDimension(
                            dimension_id=None,
                            dimension_name=dimension_name,
                            dimension_category=category,
                            alias_hits=[dimension_name],
                            supported_claim_block_ids=[],
                            evidence_block_ids=[],
                            is_new_candidate=True,
                            confidence=0.55,
                            normalization_notes=["fallback_distillation"],
                        )
                        dimensions[dimension_name] = existing
                    if block.block_type == "claim":
                        existing.supported_claim_block_ids.append(block.block_id)
                    else:
                        existing.evidence_block_ids.append(block.block_id)

        summary = {
            "content_type": "xueqiu_statuses" if records and records[0].source_type.startswith("xueqiu") else "text_records",
            "overall_confidence": 0.55 if logic_blocks else 0.2,
            "needs_review": True,
            "major_risks": ["fallback_distillation", "quoted_content_may_dominate"],
            "processing_notes": [f"records={len(records)}", f"logic_blocks={len(logic_blocks)}"],
        }
        return self.distillation_result_model(
            source_record_id=records[0].source_id,
            summary=summary,
            logic_blocks=self.dedupe_logic_blocks(logic_blocks),
            distilled_dimensions=self.merge_dimensions(list(dimensions.values())),
        ).model_dump(mode="json") | {
            "degraded": True,
            "degrade_reason": "fallback_distillation",
        }


class ChunkResultMergerSubagent:
    subagent_type = "chunk_result_merger"
    input_model = ChunkResultMergerInputModel
    output_model = DistillationEnvelopeOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Merge chunk-level distillation outputs into one run-level distillation result.",
        capabilities=("distillation_merge", "result_synthesis"),
        stage="distill",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("distill_kol_logic",),
        tags=("deterministic", "postprocess"),
    )

    def __init__(
        self,
        *,
        dedupe_logic_blocks: Callable[[list[LogicBlock]], list[LogicBlock]],
        merge_dimensions: Callable[[list[DistilledDimension]], list[DistilledDimension]],
        fallback_runner: Callable[[list[DistillSourceRecord]], dict],
        logic_block_model: type,
        distilled_dimension_model: type,
        distillation_result_model: type,
    ) -> None:
        self.dedupe_logic_blocks = dedupe_logic_blocks
        self.merge_dimensions = merge_dimensions
        self.fallback_runner = fallback_runner
        self.logic_block_model = logic_block_model
        self.distilled_dimension_model = distilled_dimension_model
        self.distillation_result_model = distillation_result_model

    def run(self, *, records: list[DistillSourceRecord], chunk_results: list[dict]) -> dict:
        merged_logic_blocks: list[LogicBlock] = []
        dimensions_by_name: dict[str, DistilledDimension] = {}
        content_types: set[str] = set()
        major_risks: list[str] = []
        processing_notes: list[str] = []
        confidence_values: list[float] = []
        needs_review = False

        for chunk_result in chunk_results:
            normalized_chunk_result = (
                chunk_result.model_dump(mode="python")
                if isinstance(chunk_result, BaseModel)
                else dict(chunk_result)
            )
            summary = normalized_chunk_result.get("summary") or {}
            content_type = summary.get("content_type")
            if content_type:
                content_types.add(str(content_type))
            needs_review = needs_review or bool(summary.get("needs_review"))
            confidence = summary.get("overall_confidence")
            if isinstance(confidence, (int, float)):
                confidence_values.append(float(confidence))
            for risk in summary.get("major_risks") or []:
                risk_value = str(risk)
                if risk_value not in major_risks:
                    major_risks.append(risk_value)
            for note in summary.get("processing_notes") or []:
                note_value = str(note)
                if note_value not in processing_notes:
                    processing_notes.append(note_value)

            for block_payload in normalized_chunk_result.get("logic_blocks") or []:
                merged_logic_blocks.append(self.logic_block_model.model_validate(block_payload))

            for dimension_payload in normalized_chunk_result.get("distilled_dimensions") or []:
                dimension = self.distilled_dimension_model.model_validate(dimension_payload)
                key = dimension.dimension_name.strip().lower() or dimension.dimension_name
                existing = dimensions_by_name.get(key)
                if existing is None:
                    dimensions_by_name[key] = dimension
                    continue
                for alias in dimension.alias_hits:
                    if alias not in existing.alias_hits:
                        existing.alias_hits.append(alias)
                for block_id in dimension.supported_claim_block_ids:
                    if block_id not in existing.supported_claim_block_ids:
                        existing.supported_claim_block_ids.append(block_id)
                for block_id in dimension.evidence_block_ids:
                    if block_id not in existing.evidence_block_ids:
                        existing.evidence_block_ids.append(block_id)
                for note in dimension.normalization_notes:
                    if note not in existing.normalization_notes:
                        existing.normalization_notes.append(note)
                existing.confidence = max(existing.confidence, dimension.confidence)
                existing.is_new_candidate = existing.is_new_candidate or dimension.is_new_candidate

        merged_logic_blocks = self.dedupe_logic_blocks(merged_logic_blocks)
        merged_dimensions = self.merge_dimensions(list(dimensions_by_name.values()))
        if not merged_logic_blocks:
            return self.fallback_runner(records)

        if len(content_types) == 1:
            merged_content_type = next(iter(content_types))
        elif content_types:
            merged_content_type = "mixed_records"
        else:
            merged_content_type = "text_records"
        overall_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.45
        if "fallback_distillation" in major_risks:
            overall_confidence = min(overall_confidence, 0.6)
        return self.distillation_result_model(
            source_record_id=records[0].source_id,
            summary={
                "content_type": merged_content_type,
                "overall_confidence": round(overall_confidence, 4),
                "needs_review": needs_review or any(risk.endswith("dominate") for risk in major_risks),
                "major_risks": major_risks,
                "processing_notes": processing_notes,
            },
            logic_blocks=merged_logic_blocks,
            distilled_dimensions=merged_dimensions,
        ).model_dump(mode="json")
