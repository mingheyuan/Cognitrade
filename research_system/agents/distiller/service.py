from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from research_system.agents._subagent_invoker import RuntimeBoundSubagentInvoker
from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.schemas.distillation import (
    DistillationResult,
    DistillationSummary,
    DistilledDimension,
    LogicBlock,
    SourceSpan,
)
from research_system.services.methodologies.dimensions import DimensionNormalizer
from research_system.schemas.subagents import SubagentInvocation
from research_system.subagents.distillation import (
    ChunkResultMergerSubagent,
    DistillChunkBuilderSubagent,
    DistillRecordTrimmerSubagent,
    DistillSourceNormalizerSubagent,
    FallbackChunkDistillerSubagent,
    LLMChunkDistillerSubagent,
)
from research_system.subagents.registry import SubagentRegistry


class DistillationResultPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_record_id: str
    summary: DistillationSummary
    logic_blocks: list[LogicBlock] = Field(default_factory=list)
    distilled_dimensions: list[DistilledDimension] = Field(default_factory=list)


DISTILL_EXECUTION_AUTO = "auto"
DISTILL_EXECUTION_FALLBACK = "fallback"


class DistillerService(RuntimeBoundSubagentInvoker):
    agent_name = "distiller"

    def __init__(self, llm_provider: OpenAIProvider, settings: Settings | None = None, runs_root: Path | None = None) -> None:
        self.llm_provider = llm_provider
        self.runs_root = runs_root
        taxonomy_config = {}
        if settings is not None:
            yaml_configs = settings.load_yaml_configs()
            taxonomy_config = {
                "dimensions": list((yaml_configs.get("taxonomy_dimensions") or {}).get("dimensions") or []),
                "aliases": list((yaml_configs.get("taxonomy_dimension_aliases") or {}).get("aliases") or []),
                "patterns": list((yaml_configs.get("taxonomy_dimension_patterns") or {}).get("patterns") or []),
            }
        self.dimension_normalizer = DimensionNormalizer(taxonomy_config)
        self.registry = SubagentRegistry(
            [
                DistillSourceNormalizerSubagent(extract_source_path=self._extract_source_path),
                DistillRecordTrimmerSubagent(record_rank=self._record_rank),
                DistillChunkBuilderSubagent(),
                LLMChunkDistillerSubagent(
                    llm_provider=self.llm_provider,
                    schema=DistillationResultPayload,
                    distillation_model=DistillationResult,
                ),
                FallbackChunkDistillerSubagent(
                    infer_block_type=self._infer_block_type,
                    infer_speaker_type=self._infer_speaker_type,
                    infer_attribution_label=self._infer_attribution_label,
                    extract_symbols=self._extract_symbols,
                    extract_sectors=self._extract_sectors,
                    infer_time_horizon=self._infer_time_horizon,
                    infer_direction=self._infer_direction,
                    infer_risk_flags=self._infer_risk_flags,
                    extract_dimensions=self._extract_dimensions,
                    dedupe_logic_blocks=self._dedupe_logic_blocks,
                    merge_dimensions=self._merge_dimensions,
                    distillation_result_model=DistillationResult,
                ),
                ChunkResultMergerSubagent(
                    dedupe_logic_blocks=self._dedupe_logic_blocks,
                    merge_dimensions=self._merge_dimensions,
                    fallback_runner=self._run_fallback,
                    logic_block_model=LogicBlock,
                    distilled_dimension_model=DistilledDimension,
                    distillation_result_model=DistillationResult,
                ),
            ]
        )

    def run(
        self,
        *,
        query: str,
        source_records: list[dict],
        execution_mode: str = DISTILL_EXECUTION_AUTO,
        runtime_degraded_reason: str | None = None,
    ) -> dict:
        with self.local_run_scope(
            intent="distill_kol_logic",
            query=query,
            phase="distill",
            title="distiller",
            objective="Distill source records into logic blocks and dimensions.",
        ):
            normalized_outputs, _ = self._invoke_subagent(
                SubagentInvocation(
                    subagent_type="distill_source_normalizer",
                    objective="Normalize raw distillation source records.",
                    inputs={"query": query, "source_records": source_records},
                    context={
                        "task_name": "distill:preprocess:source_normalizer",
                        "agent_path": "distiller/preprocess/source_normalizer",
                    },
                )
            )
            records = normalized_outputs["records"]
            if not records:
                return {
                    "summary": {
                        "content_type": "empty",
                        "overall_confidence": 0.0,
                        "needs_review": True,
                        "major_risks": ["missing_source_records"],
                        "processing_notes": ["No source records available for distillation."],
                    },
                    "logic_blocks": [],
                    "distilled_dimensions": [],
                    "degraded": True,
                    "degrade_reason": "missing_source_records",
                }

            trim_outputs, _ = self._invoke_subagent(
                SubagentInvocation(
                    subagent_type="distill_record_trimmer",
                    objective="Trim source records into bounded distillation input.",
                    inputs={"records": records},
                    context={
                        "task_name": "distill:preprocess:record_trimmer",
                        "agent_path": "distiller/preprocess/record_trimmer",
                    },
                )
            )
            trimmed_records = trim_outputs["records"]
            trim_notes = trim_outputs["notes"]
            force_fallback = execution_mode == DISTILL_EXECUTION_FALLBACK
            if not force_fallback:
                result = self._run_chunked(query=query, records=trimmed_records)
            else:
                result = self._run_fallback(records=trimmed_records)
                if force_fallback:
                    result["degraded"] = True
                    result["degrade_reason"] = runtime_degraded_reason or "turn_llm_budget_exhausted"
            result["summary"]["processing_notes"].extend(trim_notes)
            return result

    def _run_chunked(self, *, query: str, records: list[DistillSourceRecord]) -> dict:
        chunk_outputs, _ = self._invoke_subagent(
            SubagentInvocation(
                subagent_type="distill_chunk_builder",
                objective="Split trimmed records into distillation chunks.",
                inputs={"records": records},
                context={
                    "task_name": "distill:chunking:chunk_builder",
                    "agent_path": "distiller/chunking/chunk_builder",
                },
            )
        )
        chunks = chunk_outputs["chunks"]
        if not chunks:
            return self._run_fallback(records=records)

        chunk_results: list[dict] = []
        llm_successes = 0
        fallback_chunks = 0
        llm_timeout_seen = False

        for index, chunk in enumerate(chunks, start=1):
            chunk_label = f"chunk_{index}_of_{len(chunks)}"
            child_thread, child_turn = self.spawn_child_thread(
                intent="distill_chunk",
                objective=f"distill:{chunk_label}",
                agent=self.agent_name,
                phase="distill_chunk",
            )
            llm_outputs, _ = self._invoke_subagent(
                SubagentInvocation(
                    subagent_type="llm_chunk_distiller",
                    objective=f"Distill {chunk_label} with LLM.",
                    inputs={"query": f"{query}\nChunk: {chunk_label}", "records": chunk},
                    context={
                        "task_name": f"distill:chunks:{chunk_label}:llm_chunk_distiller",
                        "agent_path": f"distiller/chunks/{chunk_label}/llm_chunk_distiller",
                        "child_thread_id": getattr(child_thread, "thread_id", None),
                        "child_turn_id": getattr(child_turn, "turn_id", None),
                    },
                    requires_llm=True,
                    fallback_subagent_type="fallback_chunk_distiller",
                    fallback_objective=f"Fallback-distill {chunk_label}.",
                    fallback_inputs={"records": chunk},
                    fallback_context={
                        "task_name": f"distill:chunks:{chunk_label}:fallback_chunk_distiller",
                        "agent_path": f"distiller/chunks/{chunk_label}/fallback_chunk_distiller",
                        "child_thread_id": getattr(child_thread, "thread_id", None),
                        "child_turn_id": getattr(child_turn, "turn_id", None),
                    },
                    error_fallback_subagent_type="fallback_chunk_distiller",
                    error_fallback_objective=f"Fallback-distill {chunk_label}.",
                    error_fallback_inputs={"records": chunk},
                    error_fallback_context={
                        "task_name": f"distill:chunks:{chunk_label}:fallback_chunk_distiller",
                        "agent_path": f"distiller/chunks/{chunk_label}/fallback_chunk_distiller",
                        "child_thread_id": getattr(child_thread, "thread_id", None),
                        "child_turn_id": getattr(child_turn, "turn_id", None),
                    },
                )
            )
            chunk_result = llm_outputs
            degrade_reason = str(chunk_result.get("degrade_reason") or "").strip()
            degrade_tokens = {token.strip() for token in degrade_reason.split(";") if token.strip()}
            used_fallback = bool(chunk_result.get("degraded")) and bool(
                degrade_tokens & {"turn_llm_budget_exhausted", "fallback_distillation", "llm_request_timeout", "llm_unavailable"}
            )
            chunk_result["summary"]["processing_notes"].append(
                f"{chunk_label}:{'fallback' if used_fallback else 'llm'}"
            )
            if child_thread is not None:
                chunk_result["summary"]["processing_notes"].append(f"{chunk_label}:child_thread={child_thread.thread_id}")
            if used_fallback:
                if "llm_request_timeout" in degrade_tokens:
                    chunk_result["summary"]["processing_notes"].append(f"{chunk_label}:llm_timeout")
                    llm_timeout_seen = True
                fallback_chunks += 1
            else:
                llm_successes += 1

            chunk_results.append(chunk_result)

        merged_outputs, _ = self._invoke_subagent(
            SubagentInvocation(
                subagent_type="chunk_result_merger",
                objective="Merge chunk-level distillation outputs.",
                inputs={"records": records, "chunk_results": chunk_results},
                context={
                    "task_name": "distill:postprocess:chunk_result_merger",
                    "agent_path": "distiller/postprocess/chunk_result_merger",
                },
            )
        )
        merged = merged_outputs
        merged["summary"]["processing_notes"].extend(
            [
                f"chunk_count={len(chunks)}",
                f"llm_chunk_successes={llm_successes}",
                f"fallback_chunks={fallback_chunks}",
            ]
        )
        child_thread_ids = sorted(
            {
                note.split("=", 1)[1].strip()
                for chunk_result in chunk_results
                for note in list((chunk_result.get("summary") or {}).get("processing_notes") or [])
                if isinstance(note, str) and ":child_thread=" in note and note.split("=", 1)[1].strip()
            }
        )
        if child_thread_ids:
            merged["summary"]["processing_notes"].append(f"child_threads={','.join(child_thread_ids)}")
        if 0 < fallback_chunks < len(chunks):
            merged["summary"]["processing_notes"].append("partial_llm_distillation")
            merged["degraded"] = True
            merged["degrade_reason"] = "partial_llm_distillation"
        if fallback_chunks > 0 and "fallback_distillation" not in merged["summary"]["major_risks"]:
            merged["summary"]["major_risks"].append("fallback_distillation")
            merged["degraded"] = True
            merged["degrade_reason"] = str(merged.get("degrade_reason") or "fallback_distillation")
        if llm_timeout_seen and "llm_timeout" not in merged["summary"]["major_risks"]:
            merged["summary"]["major_risks"].append("llm_timeout")
            merged["degraded"] = True
            existing_reason = str(merged.get("degrade_reason") or "").strip()
            merged["degrade_reason"] = existing_reason or "llm_timeout"
        return merged

    def _run_fallback(self, *, records: list[DistillSourceRecord]) -> dict:
        logic_blocks: list[LogicBlock] = []
        dimensions: dict[str, DistilledDimension] = {}

        for record in records[:10]:
            lines = [line.strip() for line in record.content.splitlines() if line.strip()]
            if not lines:
                continue
            for index, line in enumerate(lines[:4], start=1):
                block_type = self._infer_block_type(line)
                block = LogicBlock(
                    block_type=block_type,
                    source_record_id=record.source_id,
                    source_span=SourceSpan(start_line=index, end_line=index, excerpt=line[:240]),
                    speaker_type=self._infer_speaker_type(line),
                    attribution_label=self._infer_attribution_label(line),
                    text=line[:500],
                    normalized_claim=line[:200] if block_type == "claim" else None,
                    symbols=self._extract_symbols(line),
                    sectors=self._extract_sectors(line),
                    time_horizon=self._infer_time_horizon(line),
                    direction=self._infer_direction(line),
                    evidence_refs=[{"source_record_id": record.source_id, "line": index}],
                    risk_flags=self._infer_risk_flags(line),
                    confidence=0.55 if block_type == "claim" else 0.6,
                )
                logic_blocks.append(block)
                for dimension_name, category in self._extract_dimensions(line):
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

        summary = DistillationSummary(
            content_type="xueqiu_statuses" if records and records[0].source_type.startswith("xueqiu") else "text_records",
            overall_confidence=0.55 if logic_blocks else 0.2,
            needs_review=True,
            major_risks=["fallback_distillation", "quoted_content_may_dominate"],
            processing_notes=[f"records={len(records)}", f"logic_blocks={len(logic_blocks)}"],
        )
        return DistillationResult(
            source_record_id=records[0].source_id,
            summary=summary,
            logic_blocks=self._dedupe_logic_blocks(logic_blocks),
            distilled_dimensions=self._merge_dimensions(list(dimensions.values())),
        ).model_dump(mode="json") | {
            "degraded": True,
            "degrade_reason": "fallback_distillation",
        }

    def _extract_source_path(self, query: str) -> Path | None:
        match = re.search(r"([A-Za-z]:[\\/][^\\/:*?\"<>|\r\n]+(?:[\\/][^\\/:*?\"<>|\r\n]+)*\.jsonl)", query)
        if not match:
            return None
        return Path(match.group(1))

    def _trim_records(self, records: list[DistillSourceRecord]) -> tuple[list[DistillSourceRecord], list[str]]:
        max_records = 6
        max_chars = 6000
        ranked = sorted(records, key=self._record_rank, reverse=True)

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
        return selected, notes

    def _record_rank(self, record: DistillSourceRecord) -> float:
        metadata = record.metadata or {}
        engagement = float(metadata.get("reply_count") or 0) + float(metadata.get("like_count") or 0) * 0.5
        keyword_bonus = 0.0
        for keyword in (
            "AI",
            "\u521b\u65b0\u836f",
            "\u4e34\u5e8a",
            "\u98ce\u9669",
            "\u6210\u672c",
            "\u6548\u7387",
            "\u51fa\u6d77",
            "\u4e92\u8054\u7f51",
        ):
            if keyword.lower() in record.content.lower():
                keyword_bonus += 1.0
        length_bonus = min(len(record.content) / 300.0, 3.0)
        return engagement + keyword_bonus + length_bonus

    def _infer_block_type(self, text: str) -> str:
        lowered = text.lower()
        if any(
            token in text
            for token in ("\u98ce\u9669", "\u4e0d\u786e\u5b9a", "\u6270\u52a8", "\u62c5\u5fe7")
        ):
            return "risk"
        if any(
            token in text
            for token in ("\u4e34\u5e8a", "\u83b7\u6279", "\u53d7\u7406", "\u51fa\u6d77", "\u5408\u4f5c", "\u50ac\u5316")
        ):
            return "catalyst"
        if any(token in lowered for token in ("\u56e0\u4e3a", "\u6240\u4ee5", "\u7531\u4e8e", "\u8bf4\u660e")):
            return "evidence"
        return "claim"

    def _extract_symbols(self, text: str) -> list[str]:
        return re.findall(r"\b\d{6}\b", text)

    def _extract_sectors(self, text: str) -> list[str]:
        hits: list[str] = []
        for sector in (
            "\u521b\u65b0\u836f",
            "\u4e92\u8054\u7f51",
            "AI",
            "\u534a\u5bfc\u4f53",
            "\u6d88\u8d39",
            "\u65b0\u80fd\u6e90",
        ):
            if sector.lower() in text.lower():
                hits.append(sector)
        return hits

    def _infer_time_horizon(self, text: str) -> str | None:
        for marker in (
            "\u77ed\u671f",
            "\u4e2d\u671f",
            "\u957f\u671f",
            "\u4eca\u5e74",
            "\u660e\u5e74",
            "\u672a\u6765",
        ):
            if marker in text:
                return marker
        return None

    def _infer_direction(self, text: str) -> str | None:
        if any(
            token in text
            for token in ("\u63d0\u5347", "\u589e\u957f", "\u6539\u5584", "\u770b\u597d", "\u53d7\u76ca")
        ):
            return "bullish"
        if any(
            token in text
            for token in ("\u4e0b\u6ed1", "\u4e0b\u964d", "\u538b\u529b", "\u98ce\u9669", "\u62c5\u5fe7")
        ):
            return "bearish"
        if any(token in text for token in ("\u4e2d\u6027", "\u4e00\u822c", "\u5e73\u66ff")):
            return "neutral"
        return "uncertain"

    def _infer_risk_flags(self, text: str) -> list[str]:
        flags: list[str] = []
        stripped = text.strip()
        if "//" in text or "\u56de\u590d@" in text or stripped.startswith(">"):
            flags.append("quoted_content_present")
        if stripped.startswith("\u56de\u590d@"):
            flags.append("reply_attribution")
        if stripped.startswith("//"):
            flags.append("repost_attribution")
        if stripped.startswith(">"):
            flags.append("explicit_quote_marker")
        if any(marker in stripped for marker in ("\u5f15\u7528", "\u8f6c\u53d1", "\u6587\u4e2d\u63d0\u5230")):
            flags.append("reported_attribution")
        if "?" in text or "\uff1f" in text:
            flags.append("question_or_uncertain_context")
        if len(text) < 20:
            flags.append("low_information_density")
        return flags

    def _infer_speaker_type(self, text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("\u56de\u590d@"):
            return "reply"
        if stripped.startswith("//"):
            return "repost"
        if stripped.startswith(">"):
            return "quoted"
        if any(marker in stripped for marker in ("\u5f15\u7528", "\u8f6c\u53d1", "\u6587\u4e2d\u63d0\u5230")):
            return "reported"
        return "author"

    def _infer_attribution_label(self, text: str) -> str | None:
        stripped = text.strip()
        if stripped.startswith("\u56de\u590d@"):
            return "reply_to_other_user"
        if stripped.startswith("//"):
            return "repost_or_forward"
        if stripped.startswith(">"):
            return "explicit_quote"
        if any(marker in stripped for marker in ("\u5f15\u7528", "\u6587\u4e2d\u63d0\u5230")):
            return "reported_speech"
        if "\u8f6c\u53d1" in stripped:
            return "forward_reference"
        return "author_original"

    def _extract_dimensions(self, text: str) -> list[tuple[str, str]]:
        return self.dimension_normalizer.extract_dimensions(text)

    def _dedupe_logic_blocks(self, logic_blocks: list[LogicBlock]) -> list[LogicBlock]:
        deduped: list[LogicBlock] = []
        best_index_by_key: dict[str, int] = {}

        for block in logic_blocks:
            key = self._claim_key(block)
            if key is None:
                deduped.append(block)
                continue

            existing_index = best_index_by_key.get(key)
            if existing_index is None:
                best_index_by_key[key] = len(deduped)
                deduped.append(block)
                continue

            existing = deduped[existing_index]
            if self._prefer_logic_block(candidate=block, existing=existing):
                replacement = self._merge_duplicate_logic_block(primary=block, duplicate=existing)
                deduped[existing_index] = replacement
            else:
                replacement = self._merge_duplicate_logic_block(primary=existing, duplicate=block)
                deduped[existing_index] = replacement

        return deduped

    def _merge_dimensions(self, dimensions: list[DistilledDimension]) -> list[DistilledDimension]:
        merged: dict[str, DistilledDimension] = {}
        for dimension in dimensions:
            normalized_dimension = self.dimension_normalizer.normalize_dimension(
                name=dimension.dimension_name,
                category=dimension.dimension_category,
                alias_hits=dimension.alias_hits,
                normalization_notes=dimension.normalization_notes,
            )
            canonical_name = str(normalized_dimension["canonical_dimension_name"])
            key = str(normalized_dimension["dimension_key"] or self._dimension_key(canonical_name))
            existing = merged.get(key)
            dimension.dimension_name = canonical_name
            dimension.canonical_dimension_name = canonical_name
            dimension.dimension_family = str(normalized_dimension["dimension_family"] or "")
            dimension.dimension_category = str(normalized_dimension["dimension_category"] or dimension.dimension_category)
            dimension.scenario_tags = list(normalized_dimension["scenario_tags"] or [])
            dimension.methodology_tags = list(normalized_dimension["methodology_tags"] or [])
            dimension.dimension_tags = list(normalized_dimension["dimension_tags"] or [])
            dimension.dimension_key = key
            dimension.normalization_notes = list(normalized_dimension["normalization_notes"] or [])
            for alias in normalized_dimension.get("canonical_aliases") or []:
                alias_value = str(alias).strip()
                if alias_value and alias_value not in dimension.alias_hits:
                    dimension.alias_hits.append(alias_value)
            if dimension.dimension_id is None:
                dimension.dimension_id = key

            if existing is None:
                if canonical_name not in dimension.alias_hits:
                    dimension.alias_hits.append(canonical_name)
                merged[key] = dimension
                continue

            for alias in dimension.alias_hits + [dimension.dimension_name]:
                raw_alias = alias.strip()
                canonical_alias = self.dimension_normalizer.canonical_dimension_name(alias)
                if raw_alias and raw_alias not in existing.alias_hits:
                    existing.alias_hits.append(raw_alias)
                if canonical_alias and canonical_alias not in existing.alias_hits:
                    existing.alias_hits.append(canonical_alias)
            for block_id in dimension.supported_claim_block_ids:
                if block_id not in existing.supported_claim_block_ids:
                    existing.supported_claim_block_ids.append(block_id)
            for block_id in dimension.evidence_block_ids:
                if block_id not in existing.evidence_block_ids:
                    existing.evidence_block_ids.append(block_id)
            for note in dimension.normalization_notes:
                if note not in existing.normalization_notes:
                    existing.normalization_notes.append(note)
            for tag in dimension.scenario_tags:
                if tag not in existing.scenario_tags:
                    existing.scenario_tags.append(tag)
            for tag in dimension.methodology_tags:
                if tag not in existing.methodology_tags:
                    existing.methodology_tags.append(tag)
            for tag in dimension.dimension_tags:
                if tag not in existing.dimension_tags:
                    existing.dimension_tags.append(tag)
            existing.confidence = max(existing.confidence, dimension.confidence)
            existing.is_new_candidate = existing.is_new_candidate or dimension.is_new_candidate

        return list(merged.values())

    def _claim_key(self, block: LogicBlock) -> str | None:
        if block.block_type != "claim":
            return None
        base = block.normalized_claim or block.text
        normalized = self._normalize_free_text(base)
        return normalized or None

    def _prefer_logic_block(self, *, candidate: LogicBlock, existing: LogicBlock) -> bool:
        candidate_score = (
            candidate.confidence,
            1 if candidate.speaker_type == "author" else 0,
            1 if candidate.attribution_label == "author_original" else 0,
            -len(candidate.risk_flags),
            len(candidate.text),
        )
        existing_score = (
            existing.confidence,
            1 if existing.speaker_type == "author" else 0,
            1 if existing.attribution_label == "author_original" else 0,
            -len(existing.risk_flags),
            len(existing.text),
        )
        return candidate_score > existing_score

    def _merge_duplicate_logic_block(self, *, primary: LogicBlock, duplicate: LogicBlock) -> LogicBlock:
        for ref in duplicate.evidence_refs:
            if ref not in primary.evidence_refs:
                primary.evidence_refs.append(ref)
        for symbol in duplicate.symbols:
            if symbol not in primary.symbols:
                primary.symbols.append(symbol)
        for sector in duplicate.sectors:
            if sector not in primary.sectors:
                primary.sectors.append(sector)
        for risk_flag in duplicate.risk_flags:
            if risk_flag not in primary.risk_flags:
                primary.risk_flags.append(risk_flag)
        if "duplicate_claim_folded" not in primary.risk_flags:
            primary.risk_flags.append("duplicate_claim_folded")
        primary.confidence = max(primary.confidence, duplicate.confidence)
        return primary

    def _dimension_key(self, name: str) -> str:
        return self.dimension_normalizer.dimension_key(name)

    def _canonical_dimension_name(self, name: str) -> str:
        return self.dimension_normalizer.canonical_dimension_name(name)

    def _normalize_free_text(self, text: str) -> str:
        lowered = unicodedata.normalize("NFKC", text).strip().lower()
        lowered = re.sub(r"[\s\-_/:,.;()\[\]{}<>]+", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered)
        return lowered.strip()

    def _merge_chunk_results(self, *, records: list[DistillSourceRecord], chunk_results: list[dict]) -> dict:
        merged_logic_blocks: list[LogicBlock] = []
        dimensions_by_name: dict[str, DistilledDimension] = {}
        content_types: set[str] = set()
        major_risks: list[str] = []
        processing_notes: list[str] = []
        confidence_values: list[float] = []
        needs_review = False

        for chunk_result in chunk_results:
            summary = chunk_result.get("summary") or {}
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

            for block_payload in chunk_result.get("logic_blocks") or []:
                merged_logic_blocks.append(LogicBlock.model_validate(block_payload))

            for dimension_payload in chunk_result.get("distilled_dimensions") or []:
                dimension = DistilledDimension.model_validate(dimension_payload)
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

        merged_logic_blocks = self._dedupe_logic_blocks(merged_logic_blocks)
        merged_dimensions = self._merge_dimensions(list(dimensions_by_name.values()))

        if not merged_logic_blocks:
            return self._run_fallback(records=records)

        if len(content_types) == 1:
            merged_content_type = next(iter(content_types))
        elif content_types:
            merged_content_type = "mixed_records"
        else:
            merged_content_type = "text_records"

        overall_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.45
        if "fallback_distillation" in major_risks:
            overall_confidence = min(overall_confidence, 0.6)

        summary = DistillationSummary(
            content_type=merged_content_type,
            overall_confidence=round(overall_confidence, 4),
            needs_review=needs_review or any(risk.endswith("dominate") for risk in major_risks),
            major_risks=major_risks,
            processing_notes=processing_notes,
        )
        return DistillationResult(
            source_record_id=records[0].source_id,
            summary=summary,
            logic_blocks=merged_logic_blocks,
            distilled_dimensions=merged_dimensions,
        ).model_dump(mode="json")
