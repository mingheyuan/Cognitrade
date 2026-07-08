from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from research_system.agents._subagent_dag import SubagentDagNodeResult
from research_system.agents._subagent_phase_controller import SubagentPhaseController
from research_system.agents._subagent_phase_policy import DistillerPhasePolicy
from research_system.agents._subagent_invoker import RuntimeBoundSubagentInvoker
from research_system.runtime.core.execution_policy import RuntimeDegradeReason
from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.schemas.distillation import (
    DistillSourceRecord,
    DistillationResult,
    DistillationSummary,
    DistilledDimension,
    LogicBlock,
    SourceSpan,
)
from research_system.services.methodologies.dimensions import DimensionNormalizer
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentGraphPatch,
    SubagentInvocation,
    SubagentPlan,
    SubagentPlanNode,
)
from research_system.subagents.distillation import (
    ChunkExecutionStrategyResolverSubagent,
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
        self.settings = settings
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
                ChunkExecutionStrategyResolverSubagent(
                    derive_chunk_execution_strategy=self._derive_chunk_execution_strategy,
                ),
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
                    fallback_runner=lambda records: self._run_fallback(records=records),
                    logic_block_model=LogicBlock,
                    distilled_dimension_model=DistilledDimension,
                    distillation_result_model=DistillationResult,
                ),
            ],
            catalog_overlay_provider=self._approved_catalog_overlay_entries,
        )
        self.phase_controller = SubagentPhaseController(
            registry=self.registry,
            invoke_subagent=self._invoke_subagent,
            emit_event=self._emit_runtime_event,
            cache_context_builder=self._subagent_dag_cache_context,
            start_from_node_id_builder=self._subagent_dag_start_from_node_id,
            runtime_binding_getter=self._runtime_binding_for_events,
            llm_provider=self.llm_provider,
            settings=self.settings,
            service_name=self.agent_name,
        )
        self.phase_policy = DistillerPhasePolicy()

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
            subagent_plans: list[dict[str, Any]] = []
            subagent_plan_patches: list[dict[str, Any]] = []
            preprocess_plan = self._build_preprocess_subagent_plan(
                query=query,
                source_records=source_records,
            )
            subagent_plans.append(preprocess_plan.model_dump(mode="json"))
            preprocess_results = self._execute_subagent_plan(
                plan=preprocess_plan,
                max_workers=1,
                dag_id="preprocess",
                build_invocation=self._build_preprocess_plan_invocations(
                    plan=preprocess_plan,
                    query=query,
                    source_records=source_records,
                ),
            )
            records = list((preprocess_results["source_normalizer"].outputs or {}).get("records") or [])
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
                    "subagent_plans": subagent_plans,
                    "subagent_plan_patches": subagent_plan_patches,
                }

            trim_outputs = preprocess_results["record_trimmer"].outputs or {}
            chunk_outputs = preprocess_results["chunk_builder"].outputs or {}
            trimmed_records = list(trim_outputs.get("records") or [])
            trim_notes = list(trim_outputs.get("notes") or [])
            chunks = list(chunk_outputs.get("chunks") or [])
            force_fallback = execution_mode == DISTILL_EXECUTION_FALLBACK
            result = self._run_chunked(
                query=query,
                records=trimmed_records,
                chunks=chunks,
                force_fallback=force_fallback,
                runtime_degraded_reason=runtime_degraded_reason,
                subagent_plans=subagent_plans,
                subagent_plan_patches=subagent_plan_patches,
            )
            result["summary"]["processing_notes"].extend(trim_notes)
            result["subagent_plans"] = subagent_plans
            result["subagent_plan_patches"] = subagent_plan_patches
            return result

    def _run_chunked(
        self,
        *,
        query: str,
        records: list[DistillSourceRecord],
        chunks: list[list[DistillSourceRecord]],
        force_fallback: bool,
        runtime_degraded_reason: str | None,
        subagent_plans: list[dict[str, Any]],
        subagent_plan_patches: list[dict[str, Any]],
    ) -> dict:
        if not chunks:
            merged = self._run_fallback(records=records)
            if force_fallback:
                merged["degraded"] = True
                merged["degrade_reason"] = (
                    runtime_degraded_reason or RuntimeDegradeReason.TURN_LLM_BUDGET_EXHAUSTED.value
                )
            return merged

        chunk_rerun_start_node = self._subagent_dag_start_from_node_id(dag_id="chunk_execution")
        direct_chunk_plan_required = bool(
            chunk_rerun_start_node
            and chunk_rerun_start_node != "chunk_execution_strategy_resolver"
        )
        if direct_chunk_plan_required:
            chunk_strategy = self._derive_chunk_execution_strategy(
                query=query,
                chunk_count=len(chunks),
                force_fallback=force_fallback,
                runtime_degraded_reason=runtime_degraded_reason,
            )
            chunk_plan = self._build_chunk_execution_subagent_plan(
                query=query,
                chunks=chunks,
                force_fallback=force_fallback,
                runtime_degraded_reason=runtime_degraded_reason,
                chunk_subagent_type=str(chunk_strategy.get("chunk_subagent_type") or "llm_chunk_distiller"),
            )
            chunk_patch_rows = []
            chunk_dag_results = self._execute_subagent_plan(
                plan=chunk_plan,
                max_workers=max(
                    len(
                        [
                            node
                            for node in chunk_plan.nodes
                            if node.subagent_type in {"llm_chunk_distiller", "fallback_chunk_distiller"}
                        ]
                    ),
                    1,
                ),
                dag_id="chunk_execution",
                build_invocation=self._build_chunk_plan_invocations(
                    plan=chunk_plan,
                    query=query,
                    records=records,
                    chunks=chunks,
                    force_fallback=force_fallback,
                    runtime_degraded_reason=runtime_degraded_reason,
                ),
            )
        else:
            chunk_plan = self._build_chunk_strategy_subagent_plan(
                query=query,
                chunks=chunks,
                force_fallback=force_fallback,
                runtime_degraded_reason=runtime_degraded_reason,
            )
            chunk_plan, _chunk_base_results, chunk_patch_rows, chunk_dag_results = self.phase_controller.execute_patch_cycle(
                plan=chunk_plan,
                dag_id="chunk_execution",
                base_max_workers=1,
                final_max_workers=max(len(chunks), 1),
                build_invocation_factory=lambda current_plan: self._build_chunk_plan_invocations(
                    plan=current_plan,
                    query=query,
                    records=records,
                    chunks=chunks,
                    force_fallback=force_fallback,
                    runtime_degraded_reason=runtime_degraded_reason,
                ),
                patch_builder=lambda base_results: self._build_chunk_execution_subagent_replan_patch(
                    chunks=chunks,
                    force_fallback=force_fallback,
                    runtime_degraded_reason=runtime_degraded_reason,
                    base_results=base_results,
                ),
                replan_builder=lambda current_plan, patch, base_results: self._rebuild_chunk_execution_subagent_plan(
                    plan=current_plan,
                    query=query,
                    chunks=chunks,
                    force_fallback=force_fallback,
                    runtime_degraded_reason=runtime_degraded_reason,
                    patch=patch,
                    base_results=base_results,
                ),
                candidate_plan_variants_builder=lambda rule_final_plan, patch, base_results: self._build_chunk_execution_candidate_plan_variants(
                    rule_final_plan=rule_final_plan,
                    query=query,
                    chunks=chunks,
                    force_fallback=force_fallback,
                    runtime_degraded_reason=runtime_degraded_reason,
                    patch=patch,
                    base_results=base_results,
                ),
            )
        subagent_plan_patches.extend(chunk_patch_rows)
        subagent_plans.append(chunk_plan.model_dump(mode="json"))

        chunk_node_ids = [
            node.node_id
            for node in chunk_plan.nodes
            if node.subagent_type in {"llm_chunk_distiller", "fallback_chunk_distiller"}
        ]
        chunk_results: list[dict] = []
        llm_successes = 0
        fallback_chunks = 0
        llm_timeout_seen = False

        for index, dag_node_id in enumerate(chunk_node_ids, start=1):
            dag_result = chunk_dag_results[dag_node_id]
            if dag_result.outputs is None:
                continue
            chunk_label = f"chunk_{index}_of_{len(chunks)}"
            chunk_result = dag_result.outputs
            degrade_reason = str(chunk_result.get("degrade_reason") or "").strip()
            degrade_tokens = {token.strip() for token in degrade_reason.split(";") if token.strip()}
            used_fallback = bool(chunk_result.get("degraded")) and bool(
                degrade_tokens
                & {
                    RuntimeDegradeReason.TURN_LLM_BUDGET_EXHAUSTED.value,
                    "fallback_distillation",
                    RuntimeDegradeReason.LLM_REQUEST_TIMEOUT.value,
                    RuntimeDegradeReason.LLM_UNAVAILABLE.value,
                }
            )
            chunk_result["summary"]["processing_notes"].append(
                f"{chunk_label}:{'fallback' if used_fallback else 'llm'}"
            )
            child_thread_id = str((dag_result.metadata or {}).get("child_thread_id") or "").strip()
            if child_thread_id:
                chunk_result["summary"]["processing_notes"].append(f"{chunk_label}:child_thread={child_thread_id}")
            if used_fallback:
                if RuntimeDegradeReason.LLM_REQUEST_TIMEOUT.value in degrade_tokens:
                    chunk_result["summary"]["processing_notes"].append(f"{chunk_label}:llm_timeout")
                    llm_timeout_seen = True
                fallback_chunks += 1
            else:
                llm_successes += 1

            chunk_results.append(chunk_result)

        merged = dict((chunk_dag_results["chunk_result_merger"].outputs or {}) or {})
        if not merged:
            merged = self._run_fallback(records=records)
        for chunk_result in chunk_results:
            for note in list((chunk_result.get("summary") or {}).get("processing_notes") or []):
                if note not in merged["summary"]["processing_notes"]:
                    merged["summary"]["processing_notes"].append(note)
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
        isolated_failures = [
            result
            for node_id, result in chunk_dag_results.items()
            if node_id in chunk_node_ids and result.status == "failed"
        ]
        if isolated_failures:
            merged["summary"]["processing_notes"].append(
                f"isolated_chunk_failures={len(isolated_failures)}"
            )
            if "isolated_chunk_failure" not in merged["summary"]["major_risks"]:
                merged["summary"]["major_risks"].append("isolated_chunk_failure")
            merged["degraded"] = True
            merged["degrade_reason"] = str(merged.get("degrade_reason") or "isolated_chunk_failure")
        if force_fallback:
            merged["degraded"] = True
            merged["degrade_reason"] = (
                runtime_degraded_reason or RuntimeDegradeReason.TURN_LLM_BUDGET_EXHAUSTED.value
            )
        return merged

    def _select_distill_catalog_entry(
        self,
        *,
        capability: str,
        preferred_subagent_type: str | None = None,
        requires_llm: bool | None = None,
    ) -> SubagentCatalogEntry:
        return self.phase_controller.select_catalog_entry(
            stage="distill",
            intent="distill_kol_logic",
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
        )

    def _cost_rank(self, cost_class: str | None) -> int:
        return self.phase_controller._cost_rank(cost_class)

    def _latency_rank(self, latency_class: str | None) -> int:
        return self.phase_controller._latency_rank(latency_class)

    def _build_catalog_plan_node(
        self,
        *,
        node_id: str,
        subagent_type: str,
        objective: str,
        inputs: dict[str, Any],
        depends_on: list[str] | None = None,
        tags: list[str] | None = None,
        failure_mode: str = "raise",
        allow_partial_dependencies: bool = False,
        max_attempts: int = 1,
        replayable: bool = True,
        cache_scope: str | None = "run",
        cache_key: str | None = None,
        notes: list[str] | None = None,
    ) -> SubagentPlanNode:
        return self.phase_controller.build_catalog_plan_node(
            node_id=node_id,
            subagent_type=subagent_type,
            objective=objective,
            inputs=inputs,
            depends_on=depends_on,
            tags=tags,
            failure_mode=failure_mode,
            allow_partial_dependencies=allow_partial_dependencies,
            max_attempts=max_attempts,
            replayable=replayable,
            cache_scope=cache_scope,
            cache_key=cache_key,
            notes=notes,
        )

    def _build_preprocess_subagent_plan(
        self,
        *,
        query: str,
        source_records: list[dict],
    ) -> SubagentPlan:
        source_entry = self._select_distill_catalog_entry(
            capability="source_normalization",
            preferred_subagent_type="distill_source_normalizer",
        )
        trimmer_entry = self._select_distill_catalog_entry(
            capability="record_trimming",
            preferred_subagent_type="distill_record_trimmer",
        )
        chunk_builder_entry = self._select_distill_catalog_entry(
            capability="chunk_building",
            preferred_subagent_type="distill_chunk_builder",
        )
        return SubagentPlan(
            intent="distill_kol_logic",
            phase="distill",
            strategy="catalog",
            nodes=[
                self._build_catalog_plan_node(
                    node_id="source_normalizer",
                    subagent_type=source_entry.subagent_type,
                    objective="Normalize raw distillation source records.",
                    inputs={"query": query, "source_record_count": len(source_records)},
                    cache_key="source_normalizer",
                    notes=["phase=preprocess"],
                ),
                self._build_catalog_plan_node(
                    node_id="record_trimmer",
                    subagent_type=trimmer_entry.subagent_type,
                    objective="Trim source records into bounded distillation input.",
                    inputs={"source_record_count": len(source_records)},
                    depends_on=["source_normalizer"],
                    cache_key="record_trimmer",
                    notes=["phase=preprocess"],
                ),
                self._build_catalog_plan_node(
                    node_id="chunk_builder",
                    subagent_type=chunk_builder_entry.subagent_type,
                    objective="Split trimmed records into distillation chunks.",
                    inputs={"source_record_count": len(source_records)},
                    depends_on=["record_trimmer"],
                    cache_key="chunk_builder",
                    notes=["phase=preprocess"],
                ),
            ],
            notes=["distillation_preprocess_catalog_plan"],
            catalog_trace={
                "stage": "distill",
                "selection_mode": "catalog",
                "phase": "preprocess",
                "selected_subagent_types": {
                    "source_normalizer": source_entry.subagent_type,
                    "record_trimmer": trimmer_entry.subagent_type,
                    "chunk_builder": chunk_builder_entry.subagent_type,
                },
            },
        )

    def _derive_chunk_execution_strategy(
        self,
        *,
        query: str,
        chunk_count: int,
        force_fallback: bool,
        runtime_degraded_reason: str | None,
    ) -> dict[str, Any]:
        _ = query
        chunk_subagent_type = "fallback_chunk_distiller" if force_fallback else "llm_chunk_distiller"
        return {
            "chunk_subagent_type": chunk_subagent_type,
            "chunk_count": chunk_count,
            "force_fallback": force_fallback,
            "runtime_degraded_reason": (
                runtime_degraded_reason or RuntimeDegradeReason.TURN_LLM_BUDGET_EXHAUSTED.value
                if force_fallback
                else runtime_degraded_reason
            ),
        }

    def _build_chunk_strategy_subagent_plan(
        self,
        *,
        query: str,
        chunks: list[list[DistillSourceRecord]],
        force_fallback: bool,
        runtime_degraded_reason: str | None,
    ) -> SubagentPlan:
        strategy_entry = self._select_distill_catalog_entry(
            capability="chunk_execution_strategy",
            preferred_subagent_type="chunk_execution_strategy_resolver",
            requires_llm=False,
        )
        return SubagentPlan(
            intent="distill_kol_logic",
            phase="distill",
            strategy="catalog",
            nodes=[
                self._build_catalog_plan_node(
                    node_id="chunk_execution_strategy_resolver",
                    subagent_type=strategy_entry.subagent_type,
                    objective="Resolve whether chunk distillation should use the LLM or fallback branch.",
                    inputs={
                        "query": query,
                        "chunk_count": len(chunks),
                        "force_fallback": force_fallback,
                        "runtime_degraded_reason": runtime_degraded_reason,
                    },
                    cache_key="chunk_execution_strategy_resolver",
                    notes=["phase=strategy"],
                )
            ],
            notes=["distillation_chunk_strategy_base_plan"],
            catalog_trace={
                "stage": "distill",
                "selection_mode": "catalog",
                "phase": "chunk_execution",
                "chunk_count": len(chunks),
                "selected_subagent_types": {
                    "chunk_execution_strategy_resolver": strategy_entry.subagent_type,
                },
            },
        )

    def _build_chunk_execution_subagent_plan(
        self,
        *,
        query: str,
        chunks: list[list[DistillSourceRecord]],
        force_fallback: bool,
        runtime_degraded_reason: str | None,
        chunk_subagent_type: str,
    ) -> SubagentPlan:
        strategy_entry = self._select_distill_catalog_entry(
            capability="chunk_execution_strategy",
            preferred_subagent_type="chunk_execution_strategy_resolver",
            requires_llm=False,
        )
        if chunk_subagent_type == "fallback_chunk_distiller":
            chunk_entry = self._select_distill_catalog_entry(
                capability="fallback_distillation",
                preferred_subagent_type="fallback_chunk_distiller",
                requires_llm=False,
            )
        else:
            chunk_entry = self._select_distill_catalog_entry(
                capability="chunk_distillation",
                preferred_subagent_type="llm_chunk_distiller",
                requires_llm=True,
            )
        merger_entry = self._select_distill_catalog_entry(
            capability="distillation_merge",
            preferred_subagent_type="chunk_result_merger",
        )
        nodes: list[SubagentPlanNode] = [
            self._build_catalog_plan_node(
                node_id="chunk_execution_strategy_resolver",
                subagent_type=strategy_entry.subagent_type,
                objective="Resolve whether chunk distillation should use the LLM or fallback branch.",
                inputs={
                    "query": query,
                    "chunk_count": len(chunks),
                    "force_fallback": force_fallback,
                    "runtime_degraded_reason": runtime_degraded_reason,
                },
                cache_key="chunk_execution_strategy_resolver",
                notes=["phase=strategy"],
            )
        ]
        selected_subagent_types: dict[str, str] = {
            "chunk_execution_strategy_resolver": strategy_entry.subagent_type,
        }
        for index, chunk_records in enumerate(chunks, start=1):
            chunk_label = f"chunk_{index}_of_{len(chunks)}"
            node_id = f"chunk_{index}"
            chunk_objective = (
                f"Fallback-distill {chunk_label}."
                if chunk_entry.subagent_type == "fallback_chunk_distiller"
                else f"Distill {chunk_label} with LLM."
            )
            chunk_notes = ["phase=chunk_execution", f"candidate={chunk_entry.subagent_type}"]
            chunk_tags = ["catalog_selected", "parallel_candidate"]
            if chunk_entry.subagent_type == "fallback_chunk_distiller":
                chunk_tags.append("fallback_selected")
            nodes.append(
                self._build_catalog_plan_node(
                    node_id=node_id,
                    subagent_type=chunk_entry.subagent_type,
                    objective=chunk_objective,
                    inputs={
                        "query": query,
                        "chunk_label": chunk_label,
                        "record_count": len(chunk_records),
                        "runtime_degraded_reason": runtime_degraded_reason,
                    },
                    depends_on=["chunk_execution_strategy_resolver"],
                    tags=chunk_tags,
                    failure_mode="isolate",
                    max_attempts=2,
                    cache_key=f"chunk:{chunk_label}",
                    notes=chunk_notes,
                )
            )
            selected_subagent_types[node_id] = chunk_entry.subagent_type
        nodes.append(
            self._build_catalog_plan_node(
                node_id="chunk_result_merger",
                subagent_type=merger_entry.subagent_type,
                objective="Merge chunk-level distillation outputs.",
                inputs={"chunk_count": len(chunks), "record_count": sum(len(chunk) for chunk in chunks)},
                depends_on=[f"chunk_{index}" for index in range(1, len(chunks) + 1)],
                allow_partial_dependencies=True,
                cache_key="chunk_result_merger",
                notes=["phase=postprocess"],
            )
        )
        selected_subagent_types["chunk_result_merger"] = merger_entry.subagent_type
        return SubagentPlan(
            intent="distill_kol_logic",
            phase="distill",
            strategy="catalog",
            nodes=nodes,
            notes=[
                "distillation_chunk_execution_catalog_plan",
                f"chunk_subagent_type={chunk_entry.subagent_type}",
            ],
            catalog_trace={
                "stage": "distill",
                "selection_mode": "catalog",
                "phase": "chunk_execution",
                "chunk_count": len(chunks),
                "selected_subagent_types": selected_subagent_types,
            },
        )

    def _build_chunk_execution_subagent_replan_patch(
        self,
        *,
        chunks: list[list[DistillSourceRecord]],
        force_fallback: bool,
        runtime_degraded_reason: str | None,
        base_results: dict[str, SubagentDagNodeResult],
    ) -> SubagentGraphPatch:
        strategy_outputs = dict(
            ((base_results["chunk_execution_strategy_resolver"].outputs or {}).get("execution_strategy") or {})
        )
        chunk_subagent_type = str(strategy_outputs.get("chunk_subagent_type") or "llm_chunk_distiller").strip()
        return self.phase_policy.build_chunk_execution_replan_patch(
            chunk_subagent_type=chunk_subagent_type,
            chunk_count=len(chunks),
            force_fallback=force_fallback,
            runtime_degraded_reason=str(strategy_outputs.get("runtime_degraded_reason") or runtime_degraded_reason or "").strip() or None,
        )

    def _rebuild_chunk_execution_subagent_plan(
        self,
        *,
        plan: SubagentPlan,
        query: str,
        chunks: list[list[DistillSourceRecord]],
        force_fallback: bool,
        runtime_degraded_reason: str | None,
        patch: SubagentGraphPatch,
        base_results: dict[str, SubagentDagNodeResult],
    ) -> SubagentPlan:
        _ = plan
        metadata = dict((patch.operations[0].metadata or {}) if patch.operations else {})
        strategy_outputs = dict(
            ((base_results["chunk_execution_strategy_resolver"].outputs or {}).get("execution_strategy") or {})
        )
        chunk_subagent_type = str(
            metadata.get("chunk_subagent_type")
            or strategy_outputs.get("chunk_subagent_type")
            or "llm_chunk_distiller"
        ).strip()
        return self._build_chunk_execution_subagent_plan(
            query=query,
            chunks=chunks,
            force_fallback=force_fallback,
            runtime_degraded_reason=str(
                metadata.get("runtime_degraded_reason")
                or strategy_outputs.get("runtime_degraded_reason")
                or runtime_degraded_reason
                or ""
            ).strip()
            or None,
            chunk_subagent_type=chunk_subagent_type,
        )

    def _build_chunk_execution_candidate_plan_variants(
        self,
        *,
        rule_final_plan: SubagentPlan,
        query: str,
        chunks: list[list[DistillSourceRecord]],
        force_fallback: bool,
        runtime_degraded_reason: str | None,
        patch: SubagentGraphPatch | None,
        base_results: dict[str, SubagentDagNodeResult],
    ) -> dict[str, SubagentPlan]:
        metadata = dict((patch.operations[0].metadata or {}) if patch is not None and patch.operations else {})
        strategy_outputs = dict(
            ((base_results["chunk_execution_strategy_resolver"].outputs or {}).get("execution_strategy") or {})
        )
        rule_chunk_subagent_type = self._chunk_execution_subagent_type_from_plan(rule_final_plan)
        runtime_reason = str(
            metadata.get("runtime_degraded_reason")
            or strategy_outputs.get("runtime_degraded_reason")
            or runtime_degraded_reason
            or ""
        ).strip() or None
        variants: dict[str, SubagentPlan] = {rule_chunk_subagent_type: rule_final_plan}
        candidate_chunk_types = ["fallback_chunk_distiller"]
        if not force_fallback and not runtime_reason:
            candidate_chunk_types.append("llm_chunk_distiller")
        for chunk_subagent_type in candidate_chunk_types:
            if chunk_subagent_type == rule_chunk_subagent_type:
                continue
            variants[chunk_subagent_type] = self._build_chunk_execution_subagent_plan(
                query=query,
                chunks=chunks,
                force_fallback=force_fallback,
                runtime_degraded_reason=runtime_reason,
                chunk_subagent_type=chunk_subagent_type,
            )
            variants[chunk_subagent_type].notes = self._unique_notes(
                [
                    *list(variants[chunk_subagent_type].notes or []),
                    "distillation_chunk_candidate_variant",
                    f"chunk_subagent_type={chunk_subagent_type}",
                ]
            )
        return variants

    def _chunk_execution_subagent_type_from_plan(self, plan: SubagentPlan) -> str:
        for node in plan.nodes:
            if node.subagent_type in {"llm_chunk_distiller", "fallback_chunk_distiller"}:
                return node.subagent_type
        return "llm_chunk_distiller"

    @staticmethod
    def _unique_notes(notes: list[str]) -> list[str]:
        unique: list[str] = []
        for note in notes:
            normalized = str(note or "").strip()
            if not normalized or normalized in unique:
                continue
            unique.append(normalized)
        return unique

    def _execute_subagent_plan(
        self,
        *,
        plan: SubagentPlan,
        max_workers: int | None,
        dag_id: str,
        build_invocation,
    ) -> dict[str, SubagentDagNodeResult]:
        return self.phase_controller.execute_plan(
            plan=plan,
            build_invocation=build_invocation,
            dag_id=dag_id,
            max_workers=max_workers,
        )

    def _build_preprocess_plan_invocations(
        self,
        *,
        plan: SubagentPlan,
        query: str,
        source_records: list[dict],
    ) -> dict[str, dict[str, Any]]:
        _ = plan
        return {
            "source_normalizer": {
                "builder": lambda _results: self._build_subagent_invocation(
                    subagent_type="distill_source_normalizer",
                    objective="Normalize raw distillation source records.",
                    inputs={"query": query, "source_records": source_records},
                    task_name="distill:preprocess:source_normalizer",
                    agent_path="distiller/preprocess/source_normalizer",
                ),
                "metadata": self._dag_metadata(
                    task_name="distill:preprocess:source_normalizer",
                    agent_path="distiller/preprocess/source_normalizer",
                    objective="Normalize raw distillation source records.",
                ),
            },
            "record_trimmer": {
                "builder": lambda results: self._build_subagent_invocation(
                    subagent_type="distill_record_trimmer",
                    objective="Trim source records into bounded distillation input.",
                    inputs={
                        "records": list(
                            ((results["source_normalizer"].outputs or {}).get("records") or [])
                        ),
                    },
                    task_name="distill:preprocess:record_trimmer",
                    agent_path="distiller/preprocess/record_trimmer",
                ),
                "metadata": self._dag_metadata(
                    task_name="distill:preprocess:record_trimmer",
                    agent_path="distiller/preprocess/record_trimmer",
                    objective="Trim source records into bounded distillation input.",
                ),
            },
            "chunk_builder": {
                "builder": lambda results: self._build_subagent_invocation(
                    subagent_type="distill_chunk_builder",
                    objective="Split trimmed records into distillation chunks.",
                    inputs={
                        "records": list(
                            ((results["record_trimmer"].outputs or {}).get("records") or [])
                        ),
                    },
                    task_name="distill:chunking:chunk_builder",
                    agent_path="distiller/chunking/chunk_builder",
                ),
                "metadata": self._dag_metadata(
                    task_name="distill:chunking:chunk_builder",
                    agent_path="distiller/chunking/chunk_builder",
                    objective="Split trimmed records into distillation chunks.",
                ),
            },
        }

    def _build_chunk_plan_invocations(
        self,
        *,
        plan: SubagentPlan,
        query: str,
        records: list[DistillSourceRecord],
        chunks: list[list[DistillSourceRecord]],
        force_fallback: bool,
        runtime_degraded_reason: str | None,
    ) -> dict[str, dict[str, Any]]:
        plan_node_map = {node.node_id: node for node in plan.nodes}
        runtime_nodes: dict[str, dict[str, Any]] = {}

        if "chunk_execution_strategy_resolver" in plan_node_map:
            runtime_nodes["chunk_execution_strategy_resolver"] = {
                "builder": lambda _results: self._build_subagent_invocation(
                    subagent_type="chunk_execution_strategy_resolver",
                    objective="Resolve whether chunk distillation should use the LLM or fallback branch.",
                    inputs={
                        "query": query,
                        "chunk_count": len(chunks),
                        "force_fallback": force_fallback,
                        "runtime_degraded_reason": runtime_degraded_reason,
                    },
                    task_name="distill:strategy:chunk_execution_strategy_resolver",
                    agent_path="distiller/strategy/chunk_execution_strategy_resolver",
                ),
                "metadata": self._dag_metadata(
                    task_name="distill:strategy:chunk_execution_strategy_resolver",
                    agent_path="distiller/strategy/chunk_execution_strategy_resolver",
                    objective="Resolve whether chunk distillation should use the LLM or fallback branch.",
                ),
            }

        for index, chunk_records in enumerate(chunks, start=1):
            node_id = f"chunk_{index}"
            if node_id not in plan_node_map:
                continue
            chunk_label = f"chunk_{index}_of_{len(chunks)}"
            plan_node = plan_node_map[node_id]
            if plan_node.subagent_type == "llm_chunk_distiller":
                child_thread, child_turn = self.spawn_child_thread(
                    intent="distill_chunk",
                    objective=f"distill:{chunk_label}",
                    agent=self.agent_name,
                    phase="distill_chunk",
                )
                runtime_nodes[node_id] = {
                    "builder": lambda _results, *, chunk=chunk_records, label=chunk_label, thread=child_thread, turn=child_turn: self._build_subagent_invocation(
                        subagent_type="llm_chunk_distiller",
                        objective=f"Distill {label} with LLM.",
                        inputs={"query": f"{query}\nChunk: {label}", "records": chunk},
                        task_name=f"distill:chunks:{label}:llm_chunk_distiller",
                        agent_path=f"distiller/chunks/{label}/llm_chunk_distiller",
                        phase="distill_chunk",
                        child_thread_id=getattr(thread, "thread_id", None),
                        child_turn_id=getattr(turn, "turn_id", None),
                        requires_llm=True,
                        fallback_subagent_type="fallback_chunk_distiller",
                        fallback_objective=f"Fallback-distill {label}.",
                        fallback_inputs={"records": chunk},
                        fallback_task_name=f"distill:chunks:{label}:fallback_chunk_distiller",
                        fallback_agent_path=f"distiller/chunks/{label}/fallback_chunk_distiller",
                        error_fallback_subagent_type="fallback_chunk_distiller",
                        error_fallback_objective=f"Fallback-distill {label}.",
                        error_fallback_inputs={"records": chunk},
                        error_fallback_task_name=f"distill:chunks:{label}:fallback_chunk_distiller",
                        error_fallback_agent_path=f"distiller/chunks/{label}/fallback_chunk_distiller",
                    ),
                    "metadata": self._dag_metadata(
                        task_name=f"distill:chunks:{chunk_label}:llm_chunk_distiller",
                        agent_path=f"distiller/chunks/{chunk_label}/llm_chunk_distiller",
                        objective=f"Distill {chunk_label} with LLM.",
                        child_thread_id=getattr(child_thread, "thread_id", None),
                        child_turn_id=getattr(child_turn, "turn_id", None),
                        phase="distill_chunk",
                    ),
                }
                continue
            if plan_node.subagent_type != "fallback_chunk_distiller":
                raise KeyError(f"unsupported distiller chunk subagent type: {plan_node.subagent_type}")
            runtime_nodes[node_id] = {
                "builder": lambda _results, *, chunk=chunk_records, label=chunk_label: self._build_subagent_invocation(
                    subagent_type="fallback_chunk_distiller",
                    objective=f"Fallback-distill {label}.",
                    inputs={"records": chunk},
                    task_name=f"distill:chunks:{label}:fallback_chunk_distiller",
                    agent_path=f"distiller/chunks/{label}/fallback_chunk_distiller",
                    phase="distill_chunk",
                ),
                "metadata": self._dag_metadata(
                    task_name=f"distill:chunks:{chunk_label}:fallback_chunk_distiller",
                    agent_path=f"distiller/chunks/{chunk_label}/fallback_chunk_distiller",
                    objective=f"Fallback-distill {chunk_label}.",
                    phase="distill_chunk",
                ),
            }

        chunk_node_ids = [
            node.node_id
            for node in plan.nodes
            if node.subagent_type in {"llm_chunk_distiller", "fallback_chunk_distiller"}
        ]
        runtime_nodes["chunk_result_merger"] = {
            "builder": lambda results: self._build_subagent_invocation(
                subagent_type="chunk_result_merger",
                objective="Merge chunk-level distillation outputs.",
                inputs={
                    "records": records,
                    "chunk_results": [
                        result.outputs
                        for node_id, result in results.items()
                        if node_id in chunk_node_ids and result.outputs is not None
                    ],
                },
                task_name="distill:postprocess:chunk_result_merger",
                agent_path="distiller/postprocess/chunk_result_merger",
            ),
            "metadata": self._dag_metadata(
                task_name="distill:postprocess:chunk_result_merger",
                agent_path="distiller/postprocess/chunk_result_merger",
                objective="Merge chunk-level distillation outputs.",
            ),
        }
        return runtime_nodes

    def _dag_metadata(
        self,
        *,
        task_name: str,
        agent_path: str,
        objective: str | None,
        child_thread_id: str | None = None,
        child_turn_id: str | None = None,
        phase: str | None = None,
    ) -> dict:
        payload = self.phase_controller.build_metadata(
            task_name=task_name,
            agent_path=agent_path,
            objective=objective,
            phase=phase or "distill",
        )
        if child_thread_id:
            payload["child_thread_id"] = child_thread_id
        if child_turn_id:
            payload["child_turn_id"] = child_turn_id
        return payload

    def _build_subagent_invocation(
        self,
        *,
        subagent_type: str,
        objective: str | None,
        inputs: dict,
        task_name: str,
        agent_path: str,
        phase: str | None = None,
        child_thread_id: str | None = None,
        child_turn_id: str | None = None,
        requires_llm: bool = False,
        fallback_subagent_type: str | None = None,
        fallback_objective: str | None = None,
        fallback_inputs: dict | None = None,
        fallback_task_name: str | None = None,
        fallback_agent_path: str | None = None,
        error_fallback_subagent_type: str | None = None,
        error_fallback_objective: str | None = None,
        error_fallback_inputs: dict | None = None,
        error_fallback_task_name: str | None = None,
        error_fallback_agent_path: str | None = None,
    ) -> SubagentInvocation:
        context = {
            "task_name": task_name,
            "agent_path": agent_path,
            "phase": phase or "distill",
        }
        if child_thread_id:
            context["child_thread_id"] = child_thread_id
        if child_turn_id:
            context["child_turn_id"] = child_turn_id

        fallback_context = {}
        if fallback_task_name or fallback_agent_path:
            fallback_context = {
                "task_name": fallback_task_name or task_name,
                "agent_path": fallback_agent_path or agent_path,
                "phase": phase or "distill",
            }
            if child_thread_id:
                fallback_context["child_thread_id"] = child_thread_id
            if child_turn_id:
                fallback_context["child_turn_id"] = child_turn_id

        error_fallback_context = {}
        if error_fallback_task_name or error_fallback_agent_path:
            error_fallback_context = {
                "task_name": error_fallback_task_name or task_name,
                "agent_path": error_fallback_agent_path or agent_path,
                "phase": phase or "distill",
            }
            if child_thread_id:
                error_fallback_context["child_thread_id"] = child_thread_id
            if child_turn_id:
                error_fallback_context["child_turn_id"] = child_turn_id

        return SubagentInvocation(
            subagent_type=subagent_type,
            objective=objective,
            inputs=inputs,
            context=context,
            requires_llm=requires_llm,
            fallback_subagent_type=fallback_subagent_type,
            fallback_objective=fallback_objective,
            fallback_inputs=dict(fallback_inputs or {}),
            fallback_context=fallback_context,
            error_fallback_subagent_type=error_fallback_subagent_type,
            error_fallback_objective=error_fallback_objective,
            error_fallback_inputs=dict(error_fallback_inputs or {}),
            error_fallback_context=error_fallback_context,
        )

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
