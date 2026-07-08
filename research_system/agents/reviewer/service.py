from __future__ import annotations

from pathlib import Path
from typing import Any

from research_system.agents._subagent_dag import SubagentDagNodeResult
from research_system.agents._subagent_phase_controller import SubagentPhaseController
from research_system.agents._subagent_phase_policy import ReviewerPhasePolicy
from research_system.agents._subagent_invoker import RuntimeBoundSubagentInvoker
from research_system.agents.reviewer.reviewer_candidate_support import ReviewerCandidateSupport
from research_system.agents.reviewer.reviewer_strategy_support import ReviewerStrategySupport
from research_system.agents.reviewer.reviewer_summary_support import ReviewerSummarySupport
from research_system.agents.reviewer.reviewer_verdict_support import ReviewerVerdictSupport
from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.schemas.review import ClaimVerdictBreakdown, ReviewFinding
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentGraphPatch,
    SubagentInvocation,
    SubagentPlan,
    SubagentPlanNode,
)
from research_system.subagents.registry import SubagentRegistry
from research_system.subagents.reviewing import (
    ReviewCandidateVerdictSubagent,
    ReviewClaimExtractorSubagent,
    ReviewContextResolverSubagent,
    ReviewFocusValidatorSubagent,
    ReviewStrategyResolverSubagent,
    ReviewSummaryBuilderSubagent,
)


class ReviewerService(RuntimeBoundSubagentInvoker):
    agent_name = "reviewer"

    def __init__(
        self,
        review_policy: dict | None = None,
        runs_root: Path | None = None,
        llm_provider: OpenAIProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.review_policy = review_policy or {}
        self.llm_provider = llm_provider
        self.settings = settings
        self.runs_root = runs_root
        self.strategy_support = ReviewerStrategySupport(self.review_policy)
        self.candidate_support = ReviewerCandidateSupport()
        self.verdict_support = ReviewerVerdictSupport(strategy_support=self.strategy_support)
        self.summary_support = ReviewerSummarySupport()
        self.registry = SubagentRegistry(
            [
                ReviewStrategyResolverSubagent(
                    normalize_planning_context_hints=self._normalize_planning_context_hints,
                    derive_review_strategy=self._derive_review_strategy,
                ),
                ReviewClaimExtractorSubagent(
                    extract_review_claims=self._extract_review_claims,
                ),
                ReviewContextResolverSubagent(
                    resolve_benchmark=self._resolve_benchmark,
                    parse_anchor_date=self._parse_anchor_date,
                    to_float=self._to_float,
                ),
                ReviewFocusValidatorSubagent(
                    validate_review_strategy_requirements=self._validate_review_strategy_requirements,
                ),
                ReviewCandidateVerdictSubagent(
                    review_candidate=self._review_candidate,
                ),
                ReviewSummaryBuilderSubagent(
                    build_review_summary=self._build_review_summary,
                ),
            ]
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
        self.phase_policy = ReviewerPhasePolicy()

    def run(
        self,
        *,
        source_run_id: str,
        query: str,
        source_entries: list[dict],
        latest_sector_snapshot: dict | None,
        latest_market_snapshot: dict | None = None,
        latest_fundamental_snapshot: dict | None = None,
        latest_notice_snapshot: dict | None = None,
        planning_context_hints: dict[str, str] | None = None,
    ) -> dict:
        with self.local_run_scope(
            intent="review_thesis",
            query=query,
            phase="review",
            title="reviewer",
            objective="Build posterior review findings and summary.",
        ):
            return self._run_impl(
                source_run_id=source_run_id,
                query=query,
                source_entries=source_entries,
                latest_sector_snapshot=latest_sector_snapshot,
                latest_market_snapshot=latest_market_snapshot,
                latest_fundamental_snapshot=latest_fundamental_snapshot,
                latest_notice_snapshot=latest_notice_snapshot,
                planning_context_hints=planning_context_hints,
            )

    def _run_impl(
        self,
        *,
        source_run_id: str,
        query: str,
        source_entries: list[dict],
        latest_sector_snapshot: dict | None,
        latest_market_snapshot: dict | None = None,
        latest_fundamental_snapshot: dict | None = None,
        latest_notice_snapshot: dict | None = None,
        planning_context_hints: dict[str, str] | None = None,
    ) -> dict:
        subagent_plan = self._build_review_subagent_plan()
        subagent_plan, _base_results, subagent_plan_patches, final_results = self.phase_controller.execute_patch_cycle(
            plan=subagent_plan,
            dag_id="review_execution",
            base_max_workers=3,
            final_max_workers=4,
            build_invocation_factory=lambda current_plan: self._build_review_plan_invocations(
                plan=current_plan,
                source_run_id=source_run_id,
                query=query,
                source_entries=source_entries,
                latest_sector_snapshot=latest_sector_snapshot,
                latest_market_snapshot=latest_market_snapshot,
                latest_fundamental_snapshot=latest_fundamental_snapshot,
                latest_notice_snapshot=latest_notice_snapshot,
                planning_context_hints=planning_context_hints,
            ),
            patch_builder=lambda base_results: self._build_review_subagent_replan_patch(base_results=base_results),
            replan_builder=lambda current_plan, _patch, base_results: self._rebuild_review_subagent_plan(
                plan=current_plan,
                source_run_id=source_run_id,
                base_results=base_results,
            ),
            candidate_plan_variants_builder=lambda rule_final_plan, _patch, base_results: self._build_review_candidate_plan_variants(
                rule_final_plan=rule_final_plan,
                source_run_id=source_run_id,
                base_results=base_results,
            ),
        )
        summary_outputs = final_results["review_summary_builder"].outputs or {}
        findings = [
            ReviewFinding.model_validate((result.outputs or {}).get("finding")).model_dump(mode="json")
            for node_id, result in final_results.items()
            if node_id.startswith("review_candidate_") and (result.outputs or {}).get("finding") is not None
        ]
        return {
            "summary": summary_outputs["summary"],
            "findings": findings,
            "subagent_trace": self._review_trace(results=final_results),
            "subagent_plans": [subagent_plan.model_dump(mode="json")],
            "subagent_plan_patches": subagent_plan_patches,
        }

    def _select_review_catalog_entry(
        self,
        *,
        capability: str,
        preferred_subagent_type: str | None = None,
    ) -> SubagentCatalogEntry:
        return self.phase_controller.select_catalog_entry(
            stage="review",
            intent="review_thesis",
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
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

    def _build_review_subagent_plan(self) -> SubagentPlan:
        strategy_entry = self._select_review_catalog_entry(
            capability="review_strategy",
            preferred_subagent_type="review_strategy_resolver",
        )
        extractor_entry = self._select_review_catalog_entry(
            capability="review_claim_extraction",
            preferred_subagent_type="review_claim_extractor",
        )
        context_entry = self._select_review_catalog_entry(
            capability="review_context",
            preferred_subagent_type="review_context_resolver",
        )
        focus_entry = self._select_review_catalog_entry(
            capability="review_focus_validation",
            preferred_subagent_type="review_focus_validator",
        )
        return SubagentPlan(
            intent="review_thesis",
            phase="review",
            strategy="catalog",
            nodes=[
                self._build_catalog_plan_node(
                    node_id="review_strategy_resolver",
                    subagent_type=strategy_entry.subagent_type,
                    objective="Resolve posterior review strategy from query and planning hints.",
                    inputs={},
                    cache_key="review_strategy_resolver",
                    notes=["phase=preprocess"],
                ),
                self._build_catalog_plan_node(
                    node_id="review_claim_extractor",
                    subagent_type=extractor_entry.subagent_type,
                    objective="Extract reviewable claims from prior run artifacts.",
                    inputs={},
                    cache_key="review_claim_extractor",
                    notes=["phase=preprocess"],
                ),
                self._build_catalog_plan_node(
                    node_id="review_context_resolver",
                    subagent_type=context_entry.subagent_type,
                    objective="Resolve benchmark and sector evidence context for posterior review.",
                    inputs={},
                    depends_on=["review_claim_extractor"],
                    cache_key="review_context_resolver",
                    notes=["phase=preprocess"],
                ),
                self._build_catalog_plan_node(
                    node_id="review_focus_validator",
                    subagent_type=focus_entry.subagent_type,
                    objective="Validate required review snapshots for the selected review focus.",
                    inputs={},
                    depends_on=["review_strategy_resolver", "review_context_resolver"],
                    cache_key="review_focus_validator",
                    notes=["phase=preprocess"],
                ),
            ],
            notes=["review_thesis_base_catalog_plan"],
            catalog_trace={
                "stage": "review",
                "selection_mode": "catalog",
                "selected_subagent_types": {
                    "review_strategy_resolver": strategy_entry.subagent_type,
                    "review_claim_extractor": extractor_entry.subagent_type,
                    "review_context_resolver": context_entry.subagent_type,
                    "review_focus_validator": focus_entry.subagent_type,
                },
            },
        )

    def _build_review_subagent_replan_patch(
        self,
        *,
        base_results: dict[str, SubagentDagNodeResult],
    ) -> SubagentGraphPatch:
        candidates = list(((base_results["review_claim_extractor"].outputs or {}).get("candidates") or []))
        review_required = self._review_required_from_results(results=base_results)
        sector_snapshot_available = bool(((base_results["review_context_resolver"].outputs or {}).get("sector_snapshot_available") or False))
        return self.phase_policy.build_review_replan_patch(
            candidate_count=len(candidates),
            review_required=review_required,
            sector_snapshot_available=sector_snapshot_available,
        )

    def _rebuild_review_subagent_plan(
        self,
        *,
        plan: SubagentPlan,
        source_run_id: str,
        base_results: dict[str, SubagentDagNodeResult],
        include_candidate_nodes: bool | None = None,
    ) -> SubagentPlan:
        candidates = list(((base_results["review_claim_extractor"].outputs or {}).get("candidates") or []))
        claim_type_counts = dict(((base_results["review_claim_extractor"].outputs or {}).get("claim_type_counts") or {}))
        review_required = self._review_required_from_results(results=base_results)
        sector_snapshot_available = bool(((base_results["review_context_resolver"].outputs or {}).get("sector_snapshot_available") or False))
        candidate_entry = self._select_review_catalog_entry(
            capability="review_candidate_verdict",
            preferred_subagent_type="review_candidate_verdict",
        )
        summary_entry = self._select_review_catalog_entry(
            capability="review_summary",
            preferred_subagent_type="review_summary_builder",
        )
        candidate_nodes: list[SubagentPlanNode] = []
        should_include_candidate_nodes = (
            bool(include_candidate_nodes)
            if include_candidate_nodes is not None
            else sector_snapshot_available and not review_required
        )
        if should_include_candidate_nodes:
            for index, candidate in enumerate(candidates, start=1):
                node_id = f"review_candidate_{index}"
                candidate_nodes.append(
                    self._build_catalog_plan_node(
                        node_id=node_id,
                        subagent_type=candidate_entry.subagent_type,
                        objective=f"Review extracted claim candidate {index}.",
                        inputs={
                            "source_run_id": source_run_id,
                            "candidate": candidate,
                        },
                        depends_on=["review_context_resolver"],
                        tags=["catalog_selected", "parallel_candidate"],
                        max_attempts=1,
                        cache_key=node_id,
                        notes=["phase=review_execution", f"claim_type={candidate.get('claim_type') or 'unknown'}"],
                    )
                )
        summary_node = self._build_catalog_plan_node(
            node_id="review_summary_builder",
            subagent_type=summary_entry.subagent_type,
            objective="Aggregate posterior review findings into review summary.",
            inputs={
                "review_required": review_required,
                "claim_type_counts": claim_type_counts,
                "candidate_count": len(candidates),
            },
            depends_on=["review_focus_validator", *[node.node_id for node in candidate_nodes]],
            cache_key="review_summary_builder",
            notes=["phase=postprocess"],
        )
        replanned = plan.model_copy(deep=True)
        replanned.nodes = [node.model_copy(deep=True) for node in plan.nodes] + [*candidate_nodes, summary_node]
        replanned.notes = self._unique_notes(
            [
                *list(replanned.notes or []),
                "review_graph_replanned_from_preprocess",
                f"review_candidate_count={len(candidates)}",
                f"review_required={str(review_required).lower()}",
                f"review_candidate_execution_enabled={str(bool(candidate_nodes)).lower()}",
            ]
        )
        replanned.catalog_trace = {
            **dict(replanned.catalog_trace or {}),
            "replanned_from_preprocess": True,
            "candidate_execution_enabled": bool(candidate_nodes),
        }
        return replanned

    def _build_review_candidate_plan_variants(
        self,
        *,
        rule_final_plan: SubagentPlan,
        source_run_id: str,
        base_results: dict[str, SubagentDagNodeResult],
    ) -> dict[str, SubagentPlan]:
        candidates = list(((base_results["review_claim_extractor"].outputs or {}).get("candidates") or []))
        sector_snapshot_available = bool(((base_results["review_context_resolver"].outputs or {}).get("sector_snapshot_available") or False))
        review_required = self._review_required_from_results(results=base_results)
        rule_variant_id = (
            "candidate_fanout"
            if any(node.node_id.startswith("review_candidate_") for node in rule_final_plan.nodes)
            else "summary_only"
        )
        variants: dict[str, SubagentPlan] = {rule_variant_id: rule_final_plan}
        if not candidates:
            return variants
        variants["summary_only"] = variants.get("summary_only") or self._rebuild_review_subagent_plan(
            plan=self._build_review_subagent_plan(),
            source_run_id=source_run_id,
            base_results=base_results,
            include_candidate_nodes=False,
        )
        variants["summary_only"].notes = self._unique_notes(
            [
                *list(variants["summary_only"].notes or []),
                "review_candidate_variant",
                "review_variant=summary_only",
            ]
        )
        if sector_snapshot_available and not review_required:
            variants["candidate_fanout"] = variants.get("candidate_fanout") or self._rebuild_review_subagent_plan(
                plan=self._build_review_subagent_plan(),
                source_run_id=source_run_id,
                base_results=base_results,
                include_candidate_nodes=True,
            )
            variants["candidate_fanout"].notes = self._unique_notes(
                [
                    *list(variants["candidate_fanout"].notes or []),
                    "review_candidate_variant",
                    "review_variant=candidate_fanout",
                ]
            )
        return variants

    def _review_required_from_results(
        self,
        *,
        results: dict[str, SubagentDagNodeResult],
    ) -> bool:
        return bool(((results["review_context_resolver"].outputs or {}).get("review_required") or False)) or bool(
            ((results["review_focus_validator"].outputs or {}).get("review_required") or False)
        )

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

    def _build_review_plan_invocations(
        self,
        *,
        plan: SubagentPlan,
        source_run_id: str,
        query: str,
        source_entries: list[dict],
        latest_sector_snapshot: dict | None,
        latest_market_snapshot: dict | None,
        latest_fundamental_snapshot: dict | None,
        latest_notice_snapshot: dict | None,
        planning_context_hints: dict[str, str] | None,
    ) -> dict[str, dict[str, Any]]:
        runtime_nodes: dict[str, dict[str, Any]] = {
            "review_strategy_resolver": {
                "builder": lambda _results: self._build_subagent_invocation(
                    subagent_type="review_strategy_resolver",
                    objective="Resolve posterior review strategy from query and planning hints.",
                    inputs={
                        "query": query,
                        "planning_context_hints": dict(planning_context_hints or {}),
                    },
                    task_name="review:strategy:review_strategy_resolver",
                    agent_path="reviewer/strategy/review_strategy_resolver",
                ),
                "metadata": self._dag_metadata(
                    task_name="review:strategy:review_strategy_resolver",
                    agent_path="reviewer/strategy/review_strategy_resolver",
                    objective="Resolve posterior review strategy from query and planning hints.",
                ),
            },
            "review_claim_extractor": {
                "builder": lambda _results: self._build_subagent_invocation(
                    subagent_type="review_claim_extractor",
                    objective="Extract reviewable claims from prior run artifacts.",
                    inputs={"source_entries": source_entries},
                    task_name="review:preprocess:review_claim_extractor",
                    agent_path="reviewer/preprocess/review_claim_extractor",
                ),
                "metadata": self._dag_metadata(
                    task_name="review:preprocess:review_claim_extractor",
                    agent_path="reviewer/preprocess/review_claim_extractor",
                    objective="Extract reviewable claims from prior run artifacts.",
                ),
            },
            "review_context_resolver": {
                "builder": lambda results: self._build_subagent_invocation(
                    subagent_type="review_context_resolver",
                    objective="Resolve benchmark and sector evidence context for posterior review.",
                    inputs={
                        "latest_sector_snapshot": latest_sector_snapshot,
                        "latest_market_snapshot": latest_market_snapshot,
                        "latest_fundamental_snapshot": latest_fundamental_snapshot,
                        "latest_notice_snapshot": latest_notice_snapshot,
                        "candidates": list(((results["review_claim_extractor"].outputs or {}).get("candidates") or [])),
                        "default_benchmark_name": str(self.review_policy.get("default_broad_index") or "HS300"),
                        "default_benchmark_symbol": "000300",
                    },
                    task_name="review:preprocess:review_context_resolver",
                    agent_path="reviewer/preprocess/review_context_resolver",
                ),
                "metadata": self._dag_metadata(
                    task_name="review:preprocess:review_context_resolver",
                    agent_path="reviewer/preprocess/review_context_resolver",
                    objective="Resolve benchmark and sector evidence context for posterior review.",
                ),
            },
            "review_focus_validator": {
                "builder": lambda results: self._build_subagent_invocation(
                    subagent_type="review_focus_validator",
                    objective="Validate required review snapshots for the selected review focus.",
                    inputs={
                        "review_strategy": dict(((results["review_strategy_resolver"].outputs or {}).get("review_strategy") or {})),
                        "latest_market_snapshot": latest_market_snapshot,
                        "latest_fundamental_snapshot": latest_fundamental_snapshot,
                        "latest_notice_snapshot": latest_notice_snapshot,
                        "policy_signals": list((((results["review_context_resolver"].outputs or {}).get("review_context") or {}).get("policy_signals") or [])),
                        "regulatory_signals": list((((results["review_context_resolver"].outputs or {}).get("review_context") or {}).get("regulatory_signals") or [])),
                    },
                    task_name="review:preprocess:review_focus_validator",
                    agent_path="reviewer/preprocess/review_focus_validator",
                ),
                "metadata": self._dag_metadata(
                    task_name="review:preprocess:review_focus_validator",
                    agent_path="reviewer/preprocess/review_focus_validator",
                    objective="Validate required review snapshots for the selected review focus.",
                ),
            },
        }

        for plan_node in plan.nodes:
            if plan_node.node_id.startswith("review_candidate_"):
                plan_node_id = plan_node.node_id
                candidate = dict(plan_node.inputs.get("candidate") or {})
                runtime_nodes[plan_node_id] = {
                    "builder": lambda results, *, claim_candidate=candidate, current_node_id=plan_node_id: self._build_subagent_invocation(
                        subagent_type="review_candidate_verdict",
                        objective="Review extracted claim candidate against benchmark-relative evidence.",
                        inputs={
                            "source_run_id": source_run_id,
                            "candidate": claim_candidate,
                            "latest_market_snapshot": latest_market_snapshot,
                            "latest_fundamental_snapshot": latest_fundamental_snapshot,
                            "latest_notice_snapshot": latest_notice_snapshot,
                            "review_context": dict(((results["review_context_resolver"].outputs or {}).get("review_context") or {})),
                        },
                        task_name=f"review:execution:{current_node_id}:review_candidate_verdict",
                        agent_path=f"reviewer/execution/{current_node_id}/review_candidate_verdict",
                    ),
                    "metadata": self._dag_metadata(
                        task_name=f"review:execution:{plan_node_id}:review_candidate_verdict",
                        agent_path=f"reviewer/execution/{plan_node_id}/review_candidate_verdict",
                        objective="Review extracted claim candidate against benchmark-relative evidence.",
                    ),
                }
            elif plan_node.node_id == "review_summary_builder":
                runtime_nodes[plan_node.node_id] = {
                    "builder": lambda results: self._build_subagent_invocation(
                        subagent_type="review_summary_builder",
                        objective="Aggregate posterior review findings into review summary.",
                        inputs={
                            "review_required": bool(((results["review_context_resolver"].outputs or {}).get("review_required") or False))
                            or bool(((results["review_focus_validator"].outputs or {}).get("review_required") or False)),
                            "findings": [
                                ReviewFinding.model_validate((result.outputs or {}).get("finding")).model_dump(mode="python")
                                for node_id, result in results.items()
                                if node_id.startswith("review_candidate_") and (result.outputs or {}).get("finding") is not None
                            ],
                            "candidates": list(((results["review_claim_extractor"].outputs or {}).get("candidates") or [])),
                            "notes": self._merge_review_notes(results=results),
                            "claim_type_counts": dict(((results["review_claim_extractor"].outputs or {}).get("claim_type_counts") or {})),
                        },
                        task_name="review:postprocess:review_summary_builder",
                        agent_path="reviewer/postprocess/review_summary_builder",
                    ),
                    "metadata": self._dag_metadata(
                        task_name="review:postprocess:review_summary_builder",
                        agent_path="reviewer/postprocess/review_summary_builder",
                        objective="Aggregate posterior review findings into review summary.",
                    ),
                }
        return runtime_nodes

    def _merge_review_notes(self, *, results: dict[str, SubagentDagNodeResult]) -> list[str]:
        notes: list[str] = []
        review_strategy = dict(((results["review_strategy_resolver"].outputs or {}).get("review_strategy") or {}))
        notes.extend([str(item) for item in list(review_strategy.get("notes") or []) if str(item).strip()])
        notes.extend([str(item) for item in list((results["review_context_resolver"].outputs or {}).get("notes") or []) if str(item).strip()])
        if not list(((results["review_claim_extractor"].outputs or {}).get("candidates") or [])):
            notes.append("no_reviewable_claims_detected")
        notes.extend([str(item) for item in list((results["review_focus_validator"].outputs or {}).get("notes") or []) if str(item).strip()])
        unique: list[str] = []
        for note in notes:
            normalized = str(note or "").strip()
            if not normalized or normalized in unique:
                continue
            unique.append(normalized)
        return unique

    def _dag_metadata(
        self,
        *,
        task_name: str,
        agent_path: str,
        objective: str | None,
    ) -> dict[str, Any]:
        return self.phase_controller.build_metadata(
            task_name=task_name,
            agent_path=agent_path,
            objective=objective,
            phase="review",
        )

    def _build_subagent_invocation(
        self,
        *,
        subagent_type: str,
        objective: str | None,
        inputs: dict[str, Any],
        task_name: str,
        agent_path: str,
    ) -> SubagentInvocation:
        return self.phase_controller.build_invocation(
            subagent_type=subagent_type,
            objective=objective,
            inputs=inputs,
            task_name=task_name,
            agent_path=agent_path,
            phase="review",
        )

    def _review_trace(self, *, results: dict[str, SubagentDagNodeResult]) -> list[dict[str, Any]]:
        return self.phase_controller.trace_from_results(results=results)

    def _normalize_planning_context_hints(self, raw_hints) -> dict[str, str]:
        return self.strategy_support.normalize_planning_context_hints(raw_hints)

    def _derive_review_strategy(
        self,
        *,
        query: str,
        planning_context_hints: dict[str, str],
    ) -> dict[str, Any]:
        return self.strategy_support.derive_review_strategy(
            query=query,
            planning_context_hints=planning_context_hints,
        )

    def _validate_review_strategy_requirements(
        self,
        *,
        review_strategy: dict[str, Any],
        latest_market_snapshot: dict | None,
        latest_fundamental_snapshot: dict | None,
        latest_notice_snapshot: dict | None,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
    ) -> dict[str, Any]:
        return self.strategy_support.validate_review_strategy_requirements(
            review_strategy=review_strategy,
            latest_market_snapshot=latest_market_snapshot,
            latest_fundamental_snapshot=latest_fundamental_snapshot,
            latest_notice_snapshot=latest_notice_snapshot,
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
        )

    def _extract_review_claims(self, *, source_entries: list[dict]) -> dict:
        return self.candidate_support.extract_review_claims(source_entries=source_entries)

    def _resolve_benchmark(
        self,
        *,
        latest_sector_snapshot: dict | None,
        latest_market_snapshot: dict | None,
        default_benchmark_name: str,
        default_benchmark_symbol: str,
    ) -> dict:
        return self.strategy_support.resolve_benchmark(
            latest_sector_snapshot=latest_sector_snapshot,
            latest_market_snapshot=latest_market_snapshot,
            default_benchmark_name=default_benchmark_name,
            default_benchmark_symbol=default_benchmark_symbol,
        )

    def _review_candidate(self, **kwargs) -> dict:
        return self.verdict_support.review_candidate(**kwargs)

    def _build_review_summary(
        self,
        *,
        review_required: bool,
        findings: list[ReviewFinding],
        candidates: list[dict],
        notes: list[str],
        claim_type_counts: dict[str, int],
    ) -> dict:
        return self.summary_support.build_review_summary(
            review_required=review_required,
            findings=findings,
            candidates=candidates,
            notes=notes,
            claim_type_counts=claim_type_counts,
        )

    def _extract_candidates(self, *, source_entries: list[dict]) -> list[dict]:
        return self.candidate_support.extract_candidates(source_entries=source_entries)

    def _extract_report_fragment_candidates(self, *, entry_id: str, payload: dict) -> list[dict]:
        return self.candidate_support.extract_report_fragment_candidates(entry_id=entry_id, payload=payload)

    def _extract_scorecard_candidates(self, *, entry_id: str, payload: dict) -> list[dict]:
        return self.candidate_support.extract_scorecard_candidates(entry_id=entry_id, payload=payload)

    def _extract_logic_block_candidates(self, *, entry_id: str, payload: dict) -> list[dict]:
        return self.candidate_support.extract_logic_block_candidates(entry_id=entry_id, payload=payload)

    def _dedupe_candidates(self, candidates: list[dict]) -> list[dict]:
        return self.candidate_support.dedupe_candidates(candidates)

    def _claim_type_counts(self, candidates: list[dict]) -> dict[str, int]:
        return self.candidate_support.claim_type_counts(candidates)

    def _verdict_by_claim_type(self, findings: list[ReviewFinding]) -> dict[str, ClaimVerdictBreakdown]:
        return self.summary_support.verdict_by_claim_type(findings)

    def _verdict_totals(self, verdict_by_claim_type: dict[str, ClaimVerdictBreakdown]) -> tuple[int, int, int]:
        return self.summary_support.verdict_totals(verdict_by_claim_type)

    def _benchmark_scope_counts(self, findings: list[ReviewFinding]) -> dict[str, int]:
        return self.summary_support.benchmark_scope_counts(findings)

    def _benchmark_scope_explanations(self, benchmark_scope_counts: dict[str, int]) -> dict[str, str]:
        return self.summary_support.benchmark_scope_explanations(benchmark_scope_counts)

    def _benchmark_scope_explanation(self, scope: str) -> str:
        return self.summary_support.benchmark_scope_explanation(scope)

    def _overall_verdict(
        self,
        *,
        review_required: bool,
        finding_count: int,
        confirmed: int,
        rejected: int,
        inconclusive: int,
    ) -> str:
        return self.summary_support.overall_verdict(
            review_required=review_required,
            finding_count=finding_count,
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
        )

    def _summary_text(
        self,
        *,
        overall_verdict: str,
        finding_count: int,
        confirmed: int,
        rejected: int,
        inconclusive: int,
        benchmark_scope_counts: dict[str, int],
    ) -> str:
        return self.summary_support.summary_text(
            overall_verdict=overall_verdict,
            finding_count=finding_count,
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
            benchmark_scope_counts=benchmark_scope_counts,
        )

    def _candidate_verdict(
        self,
        *,
        candidate: dict,
        excess_metric: float | None,
        latest_market_snapshot: dict | None,
        latest_notice_snapshot: dict | None,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> str:
        return self.verdict_support.candidate_verdict(
            candidate=candidate,
            excess_metric=excess_metric,
            latest_market_snapshot=latest_market_snapshot,
            latest_notice_snapshot=latest_notice_snapshot,
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
            theme_news=theme_news,
        )

    def _observed_metric_for_candidate(
        self,
        *,
        candidate: dict,
        latest_hist: dict,
        latest_market_snapshot: dict | None,
        latest_notice_snapshot: dict | None,
        excess_metric: float | None,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> float | None:
        return self.verdict_support.observed_metric_for_candidate(
            candidate=candidate,
            latest_hist=latest_hist,
            latest_market_snapshot=latest_market_snapshot,
            latest_notice_snapshot=latest_notice_snapshot,
            excess_metric=excess_metric,
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
            theme_news=theme_news,
        )

    def _candidate_notes(
        self,
        *,
        candidate: dict,
        sector_name: str,
        latest_market_snapshot: dict | None,
        latest_fundamental_snapshot: dict | None,
        latest_notice_snapshot: dict | None,
        setup_label: str,
        catalyst_digest: dict,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> str:
        return self.verdict_support.candidate_notes(
            candidate=candidate,
            sector_name=sector_name,
            latest_market_snapshot=latest_market_snapshot,
            latest_fundamental_snapshot=latest_fundamental_snapshot,
            latest_notice_snapshot=latest_notice_snapshot,
            setup_label=setup_label,
            catalyst_digest=catalyst_digest,
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
            theme_news=theme_news,
        )

    def _infer_expected_direction(self, text: str) -> str | None:
        return self.candidate_support.infer_expected_direction(text)

    def _expected_direction_from_setup(self, setup_label: str) -> str | None:
        return self.candidate_support.expected_direction_from_setup(setup_label)

    def _expected_direction_from_logic_block(self, *, text: str, direction: str) -> str | None:
        return self.candidate_support.expected_direction_from_logic_block(text=text, direction=direction)

    def _contains_catalyst_claim(self, text: str) -> bool:
        return self.candidate_support.contains_catalyst_claim(text)

    def _contains_time_window_claim(self, text: str) -> bool:
        return self.candidate_support.contains_time_window_claim(text)

    def _infer_window_size(self, text: str) -> int:
        return self.candidate_support.infer_window_size(text)

    def _infer_window_size_from_horizon(self, time_horizon: str, text: str) -> int:
        return self.candidate_support.infer_window_size_from_horizon(time_horizon, text)

    def _direction_verdict(self, *, expected_direction: str, market_change: float | None, excess_metric: float | None) -> str:
        return self.verdict_support.direction_verdict(
            expected_direction=expected_direction,
            market_change=market_change,
            excess_metric=excess_metric,
        )

    def _catalyst_verdict(
        self,
        *,
        latest_notice_snapshot: dict | None,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> str:
        return self.verdict_support.catalyst_verdict(
            latest_notice_snapshot=latest_notice_snapshot,
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
            theme_news=theme_news,
        )

    def _time_window_verdict(
        self,
        *,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> str:
        return self.verdict_support.time_window_verdict(
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
            theme_news=theme_news,
        )

    def _parse_anchor_date(self, *, candidates: list[dict], snapshot_date: str) -> str:
        return self.strategy_support.parse_anchor_date(candidates=candidates, snapshot_date=snapshot_date)

    def _resolve_benchmark_scope(self, *, latest_sector_snapshot: dict | None, latest_market_snapshot: dict | None) -> str:
        return self.strategy_support.resolve_benchmark_scope(
            latest_sector_snapshot=latest_sector_snapshot,
            latest_market_snapshot=latest_market_snapshot,
        )

    def _resolve_benchmark_name(
        self,
        *,
        benchmark_scope: str,
        sector_name: str,
        default_benchmark_name: str,
    ) -> str:
        return self.strategy_support.resolve_benchmark_name(
            benchmark_scope=benchmark_scope,
            sector_name=sector_name,
            default_benchmark_name=default_benchmark_name,
        )

    def _resolve_benchmark_symbol(
        self,
        *,
        benchmark_scope: str,
        latest_market_snapshot: dict | None,
        default_benchmark_symbol: str,
    ) -> str:
        return self.strategy_support.resolve_benchmark_symbol(
            benchmark_scope=benchmark_scope,
            latest_market_snapshot=latest_market_snapshot,
            default_benchmark_symbol=default_benchmark_symbol,
        )

    def _benchmark_pct_change(self, *, benchmark: dict) -> float | None:
        return self.strategy_support.benchmark_pct_change(benchmark=benchmark)

    def _market_pct_change(self, snapshot: dict | None) -> float | None:
        return self.strategy_support.market_pct_change(snapshot)

    def _notice_signal_count(self, snapshot: dict | None) -> int | None:
        return self.strategy_support.notice_signal_count(snapshot)

    def _to_float(self, value: Any) -> float | None:
        return self.strategy_support.to_float(value)

    @staticmethod
    def _unique_notes(notes: list[str]) -> list[str]:
        unique: list[str] = []
        for note in notes:
            normalized = str(note or "").strip()
            if not normalized or normalized in unique:
                continue
            unique.append(normalized)
        return unique
