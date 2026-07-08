from __future__ import annotations

from pathlib import Path
from typing import Any

from research_system.agents._subagent_dag import SubagentDagNodeResult
from research_system.agents._subagent_phase_controller import SubagentPhaseController
from research_system.agents._subagent_phase_policy import ScreenReviewerPhasePolicy
from research_system.agents._subagent_invoker import RuntimeBoundSubagentInvoker
from research_system.agents.screen_reviewer.screen_reviewer_finding_support import ScreenReviewerFindingSupport
from research_system.agents.screen_reviewer.screen_reviewer_strategy_support import ScreenReviewerStrategySupport
from research_system.agents.screen_reviewer.screen_reviewer_summary_support import ScreenReviewerSummarySupport
from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.schemas.review import ReviewFinding
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentGraphPatch,
    SubagentInvocation,
    SubagentPlan,
    SubagentPlanNode,
)
from research_system.subagents.registry import SubagentRegistry
from research_system.subagents.reviewing import (
    ScreenReviewCandidateSubagent,
    ScreenReviewStrategyResolverSubagent,
    ScreenReviewSummaryBuilderSubagent,
)


class ScreenReviewerService(RuntimeBoundSubagentInvoker):
    agent_name = "screen_reviewer"

    def __init__(
        self,
        runs_root: Path | None = None,
        llm_provider: OpenAIProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.settings = settings
        self.strategy_support = ScreenReviewerStrategySupport()
        self.finding_support = ScreenReviewerFindingSupport()
        self.summary_support = ScreenReviewerSummarySupport()
        self.runs_root = runs_root
        self.registry = SubagentRegistry(
            [
                ScreenReviewStrategyResolverSubagent(
                    normalize_planning_context_hints=self._normalize_planning_context_hints,
                    derive_review_strategy=self._derive_review_strategy,
                ),
                ScreenReviewCandidateSubagent(
                    build_finding=self.finding_support.build_finding,
                ),
                ScreenReviewSummaryBuilderSubagent(
                    build_review_summary=self.summary_support.build_review_summary,
                ),
            ],
            catalog_overlay_provider=self._approved_catalog_overlay_entries,
            activation_provider=self._activated_discovery_alias_entries,
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
        self.phase_policy = ScreenReviewerPhasePolicy()

    def run(
        self,
        *,
        source_run_id: str,
        candidates: list[dict],
        scorecard: dict | None = None,
        planning_context_hints: dict[str, str] | None = None,
    ) -> dict:
        with self.local_run_scope(
            intent="stock_screen_review",
            query=str(source_run_id or "screen_review"),
            phase="review",
            title="screen_reviewer",
            objective="Review stock screen candidates and summarize findings.",
        ):
            return self._run_impl(
                source_run_id=source_run_id,
                candidates=candidates,
                scorecard=scorecard,
                planning_context_hints=planning_context_hints,
            )

    def _run_impl(
        self,
        *,
        source_run_id: str,
        candidates: list[dict],
        scorecard: dict | None = None,
        planning_context_hints: dict[str, str] | None = None,
    ) -> dict:
        subagent_plan = self._build_screen_review_subagent_plan()
        subagent_plan, _base_results, subagent_plan_patches, final_results = self.phase_controller.execute_patch_cycle(
            plan=subagent_plan,
            dag_id="screen_review_execution",
            base_max_workers=1,
            final_max_workers=6,
            build_invocation_factory=lambda current_plan: self._build_screen_review_plan_invocations(
                plan=current_plan,
                source_run_id=source_run_id,
                candidates=candidates,
                scorecard=scorecard,
                planning_context_hints=planning_context_hints,
            ),
            patch_builder=lambda _base_results: self._build_screen_review_subagent_replan_patch(
                candidates=candidates,
            ),
            replan_builder=lambda current_plan, _patch, _base_results: self._rebuild_screen_review_subagent_plan(
                plan=current_plan,
                source_run_id=source_run_id,
                candidates=candidates,
            ),
            candidate_plan_variants_builder=lambda rule_final_plan, _patch, _base_results: self._build_screen_review_candidate_plan_variants(
                rule_final_plan=rule_final_plan,
                source_run_id=source_run_id,
                candidates=candidates,
            ),
        )
        findings = [
            ReviewFinding.model_validate((result.outputs or {}).get("finding")).model_dump(mode="json")
            for node_id, result in final_results.items()
            if node_id.startswith("screen_review_candidate_") and (result.outputs or {}).get("finding") is not None
        ]
        summary_outputs = final_results["screen_review_summary_builder"].outputs or {}
        return {
            "summary": summary_outputs["summary"],
            "findings": findings,
            "subagent_trace": self._screen_review_trace(results=final_results),
            "subagent_plans": [subagent_plan.model_dump(mode="json")],
            "subagent_plan_patches": subagent_plan_patches,
        }

    def _select_screen_review_catalog_entry(
        self,
        *,
        capability: str,
        preferred_subagent_type: str | None = None,
    ) -> SubagentCatalogEntry:
        return self.phase_controller.select_catalog_entry(
            stage="review",
            intent="stock_screen_review",
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
        max_attempts: int = 1,
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
            max_attempts=max_attempts,
            cache_scope=cache_scope,
            cache_key=cache_key,
            notes=notes,
        )

    def _build_screen_review_subagent_plan(self) -> SubagentPlan:
        strategy_entry = self._select_screen_review_catalog_entry(
            capability="screen_review_strategy",
            preferred_subagent_type="screen_review_strategy_resolver",
        )
        return SubagentPlan(
            intent="stock_screen_review",
            phase="review",
            strategy="catalog",
            nodes=[
                self._build_catalog_plan_node(
                    node_id="screen_review_strategy_resolver",
                    subagent_type=strategy_entry.subagent_type,
                    objective="Resolve lightweight screen-review strategy from candidates and planning hints.",
                    inputs={},
                    cache_key="screen_review_strategy_resolver",
                    notes=["phase=preprocess"],
                )
            ],
            notes=["screen_review_base_catalog_plan"],
            catalog_trace={
                "stage": "review",
                "selection_mode": "catalog",
                "selected_subagent_types": {
                    "screen_review_strategy_resolver": strategy_entry.subagent_type,
                },
            },
        )

    def _build_screen_review_subagent_replan_patch(
        self,
        *,
        candidates: list[dict],
    ) -> SubagentGraphPatch:
        return self.phase_policy.build_screen_review_replan_patch(
            candidate_count=len(candidates),
        )

    def _rebuild_screen_review_subagent_plan(
        self,
        *,
        plan: SubagentPlan,
        source_run_id: str,
        candidates: list[dict],
        include_candidate_nodes: bool | None = None,
    ) -> SubagentPlan:
        candidate_entry = self._select_screen_review_catalog_entry(
            capability="screen_review_candidate",
            preferred_subagent_type="screen_review_candidate",
        )
        summary_entry = self._select_screen_review_catalog_entry(
            capability="screen_review_summary",
            preferred_subagent_type="screen_review_summary_builder",
        )
        candidate_nodes: list[SubagentPlanNode] = []
        should_include_candidate_nodes = bool(include_candidate_nodes) if include_candidate_nodes is not None else bool(candidates)
        if should_include_candidate_nodes:
            for index, candidate in enumerate(candidates, start=1):
                node_id = f"screen_review_candidate_{index}"
                candidate_nodes.append(
                    self._build_catalog_plan_node(
                        node_id=node_id,
                        subagent_type=candidate_entry.subagent_type,
                        objective=f"Review screened candidate {index}.",
                        inputs={
                            "source_run_id": source_run_id,
                            "candidate": candidate,
                        },
                        tags=["catalog_selected", "parallel_candidate"],
                        cache_key=node_id,
                        notes=["phase=review_execution", f"symbol={candidate.get('symbol') or ''}"],
                    )
                )
        summary_node = self._build_catalog_plan_node(
            node_id="screen_review_summary_builder",
            subagent_type=summary_entry.subagent_type,
            objective="Aggregate screen-review findings into summary.",
            inputs={"candidate_count": len(candidates)},
            depends_on=["screen_review_strategy_resolver", *[node.node_id for node in candidate_nodes]],
            cache_key="screen_review_summary_builder",
            notes=["phase=postprocess"],
        )
        replanned = plan.model_copy(deep=True)
        replanned.nodes = [node.model_copy(deep=True) for node in plan.nodes] + [*candidate_nodes, summary_node]
        replanned.notes = self._unique_notes(
            [
                *list(replanned.notes or []),
                "screen_review_graph_replanned_from_strategy",
                f"screen_review_candidate_count={len(candidates)}",
                f"screen_review_candidate_execution_enabled={str(bool(candidate_nodes)).lower()}",
            ]
        )
        replanned.catalog_trace = {
            **dict(replanned.catalog_trace or {}),
            "replanned_from_strategy": True,
            "candidate_execution_enabled": bool(candidate_nodes),
        }
        return replanned

    def _build_screen_review_candidate_plan_variants(
        self,
        *,
        rule_final_plan: SubagentPlan,
        source_run_id: str,
        candidates: list[dict],
    ) -> dict[str, SubagentPlan]:
        rule_variant_id = (
            "candidate_fanout"
            if any(node.node_id.startswith("screen_review_candidate_") for node in rule_final_plan.nodes)
            else "summary_only"
        )
        variants: dict[str, SubagentPlan] = {rule_variant_id: rule_final_plan}
        variants["summary_only"] = variants.get("summary_only") or self._rebuild_screen_review_subagent_plan(
            plan=self._build_screen_review_subagent_plan(),
            source_run_id=source_run_id,
            candidates=candidates,
            include_candidate_nodes=False,
        )
        variants["summary_only"].notes = self._unique_notes(
            [
                *list(variants["summary_only"].notes or []),
                "screen_review_candidate_variant",
                "screen_review_variant=summary_only",
            ]
        )
        if candidates:
            variants["candidate_fanout"] = variants.get("candidate_fanout") or self._rebuild_screen_review_subagent_plan(
                plan=self._build_screen_review_subagent_plan(),
                source_run_id=source_run_id,
                candidates=candidates,
                include_candidate_nodes=True,
            )
            variants["candidate_fanout"].notes = self._unique_notes(
                [
                    *list(variants["candidate_fanout"].notes or []),
                    "screen_review_candidate_variant",
                    "screen_review_variant=candidate_fanout",
                ]
            )
        return variants

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

    def _build_screen_review_plan_invocations(
        self,
        *,
        plan: SubagentPlan,
        source_run_id: str,
        candidates: list[dict],
        scorecard: dict | None,
        planning_context_hints: dict[str, str] | None,
    ) -> dict[str, dict[str, Any]]:
        runtime_nodes: dict[str, dict[str, Any]] = {
            "screen_review_strategy_resolver": {
                "builder": lambda _results: self._build_subagent_invocation(
                    subagent_type="screen_review_strategy_resolver",
                    objective="Resolve lightweight screen-review strategy from candidates and planning hints.",
                    inputs={
                        "candidates": candidates,
                        "scorecard": scorecard,
                        "planning_context_hints": dict(planning_context_hints or {}),
                    },
                    task_name="screen_review:strategy:screen_review_strategy_resolver",
                    agent_path="screen_reviewer/strategy/screen_review_strategy_resolver",
                ),
                "metadata": self._dag_metadata(
                    task_name="screen_review:strategy:screen_review_strategy_resolver",
                    agent_path="screen_reviewer/strategy/screen_review_strategy_resolver",
                    objective="Resolve lightweight screen-review strategy from candidates and planning hints.",
                ),
            }
        }
        for plan_node in plan.nodes:
            if plan_node.node_id.startswith("screen_review_candidate_"):
                plan_node_id = plan_node.node_id
                candidate = dict(plan_node.inputs.get("candidate") or {})
                runtime_nodes[plan_node_id] = {
                    "builder": lambda _results, *, claim_candidate=candidate, current_node_id=plan_node_id: self._build_subagent_invocation(
                        subagent_type="screen_review_candidate",
                        objective="Review screened candidate for evidence completeness and caveats.",
                        inputs={
                            "source_run_id": source_run_id,
                            "candidate": claim_candidate,
                        },
                        task_name=f"screen_review:execution:{current_node_id}:screen_review_candidate",
                        agent_path=f"screen_reviewer/execution/{current_node_id}/screen_review_candidate",
                    ),
                    "metadata": self._dag_metadata(
                        task_name=f"screen_review:execution:{plan_node_id}:screen_review_candidate",
                        agent_path=f"screen_reviewer/execution/{plan_node_id}/screen_review_candidate",
                        objective="Review screened candidate for evidence completeness and caveats.",
                    ),
                }
            elif plan_node.node_id == "screen_review_summary_builder":
                runtime_nodes[plan_node.node_id] = {
                    "builder": lambda results: self._build_subagent_invocation(
                        subagent_type="screen_review_summary_builder",
                        objective="Aggregate screen-review findings into summary.",
                        inputs={
                            "candidates": candidates,
                            "findings": [
                                ReviewFinding.model_validate((result.outputs or {}).get("finding")).model_dump(mode="python")
                                for node_id, result in results.items()
                                if node_id.startswith("screen_review_candidate_") and (result.outputs or {}).get("finding") is not None
                            ],
                            "notes": self._merge_screen_review_notes(results=results),
                            "scorecard": scorecard,
                        },
                        task_name="screen_review:postprocess:screen_review_summary_builder",
                        agent_path="screen_reviewer/postprocess/screen_review_summary_builder",
                    ),
                    "metadata": self._dag_metadata(
                        task_name="screen_review:postprocess:screen_review_summary_builder",
                        agent_path="screen_reviewer/postprocess/screen_review_summary_builder",
                        objective="Aggregate screen-review findings into summary.",
                    ),
                }
        return runtime_nodes

    def _merge_screen_review_notes(self, *, results: dict[str, SubagentDagNodeResult]) -> list[str]:
        review_strategy = dict(((results["screen_review_strategy_resolver"].outputs or {}).get("review_strategy") or {}))
        notes = [str(item) for item in list(review_strategy.get("notes") or []) if str(item).strip()]
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

    def _screen_review_trace(self, *, results: dict[str, SubagentDagNodeResult]) -> list[dict[str, Any]]:
        return self.phase_controller.trace_from_results(results=results)

    def _normalize_planning_context_hints(self, raw_hints) -> dict[str, str]:
        return self.strategy_support.normalize_planning_context_hints(raw_hints)

    def _derive_review_strategy(
        self,
        *,
        candidates: list[dict],
        scorecard: dict | None,
        planning_context_hints: dict[str, str],
    ) -> dict[str, object]:
        return self.strategy_support.derive_review_strategy(
            candidates=candidates,
            scorecard=scorecard,
            planning_context_hints=planning_context_hints,
        )

    def _to_float(self, value):
        return self.finding_support.to_float(value)
