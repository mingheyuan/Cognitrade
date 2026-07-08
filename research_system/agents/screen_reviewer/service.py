from __future__ import annotations

from pathlib import Path
from typing import Any

from research_system.agents._subagent_dag import (
    SubagentDagExecutor,
    SubagentDagNodeResult,
    SubagentDagNodeSpec,
)
from research_system.agents._subagent_invoker import RuntimeBoundSubagentInvoker
from research_system.agents.screen_reviewer.screen_reviewer_finding_support import ScreenReviewerFindingSupport
from research_system.agents.screen_reviewer.screen_reviewer_strategy_support import ScreenReviewerStrategySupport
from research_system.agents.screen_reviewer.screen_reviewer_summary_support import ScreenReviewerSummarySupport
from research_system.schemas.review import ReviewFinding
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentGraphPatch,
    SubagentGraphPatchOperation,
    SubagentInvocation,
    SubagentPlan,
    SubagentPlanNode,
)
from research_system.subagents.planning import apply_subagent_graph_patch
from research_system.subagents.registry import SubagentRegistry
from research_system.subagents.reviewing import (
    ScreenReviewCandidateSubagent,
    ScreenReviewStrategyResolverSubagent,
    ScreenReviewSummaryBuilderSubagent,
)


class ScreenReviewerService(RuntimeBoundSubagentInvoker):
    agent_name = "screen_reviewer"

    def __init__(self, runs_root: Path | None = None) -> None:
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
            ]
        )

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
        base_results = self._execute_subagent_plan(
            plan=subagent_plan,
            max_workers=1,
            dag_id="screen_review_execution",
            build_invocation=self._build_screen_review_plan_invocations(
                plan=subagent_plan,
                source_run_id=source_run_id,
                candidates=candidates,
                scorecard=scorecard,
                planning_context_hints=planning_context_hints,
            ),
        )
        subagent_plan_patch = self._build_screen_review_subagent_plan_patch(
            source_run_id=source_run_id,
            candidates=candidates,
        )
        subagent_plan_patches: list[dict[str, Any]] = []
        if subagent_plan_patch is not None:
            subagent_plan = apply_subagent_graph_patch(subagent_plan, subagent_plan_patch)
            subagent_plan_patches.append(subagent_plan_patch.model_dump(mode="json"))
        final_results = self._execute_subagent_plan(
            plan=subagent_plan,
            max_workers=6,
            dag_id="screen_review_execution",
            build_invocation=self._build_screen_review_plan_invocations(
                plan=subagent_plan,
                source_run_id=source_run_id,
                candidates=candidates,
                scorecard=scorecard,
                planning_context_hints=planning_context_hints,
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
        candidates = self.registry.catalog_entries(
            stage="review",
            intent="stock_screen_review",
            capability=capability,
        )
        if not candidates:
            candidates = self.registry.catalog_entries(stage="review", capability=capability)
        if not candidates:
            raise KeyError(f"no screen reviewer catalog entry for capability={capability!r}")
        if preferred_subagent_type:
            for candidate in candidates:
                if candidate.subagent_type == preferred_subagent_type:
                    return candidate
        return sorted(
            candidates,
            key=lambda candidate: (
                self._cost_rank(candidate.cost_class),
                self._latency_rank(candidate.latency_class),
                candidate.subagent_type,
            ),
        )[0]

    def _cost_rank(self, cost_class: str | None) -> int:
        return {"low": 0, "medium": 1, "high": 2}.get(str(cost_class or "").strip().lower(), 3)

    def _latency_rank(self, latency_class: str | None) -> int:
        return {"low": 0, "medium": 1, "high": 2}.get(str(latency_class or "").strip().lower(), 3)

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
        return SubagentPlanNode(
            node_id=node_id,
            subagent_type=subagent_type,
            objective=objective,
            inputs=dict(inputs),
            depends_on=list(depends_on or []),
            tags=list(tags or ["catalog_selected"]),
            max_attempts=max_attempts,
            replayable=True,
            cache_scope=cache_scope,  # type: ignore[arg-type]
            cache_key=cache_key,
            notes=list(notes or []),
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

    def _build_screen_review_subagent_plan_patch(
        self,
        *,
        source_run_id: str,
        candidates: list[dict],
    ) -> SubagentGraphPatch:
        candidate_entry = self._select_screen_review_catalog_entry(
            capability="screen_review_candidate",
            preferred_subagent_type="screen_review_candidate",
        )
        summary_entry = self._select_screen_review_catalog_entry(
            capability="screen_review_summary",
            preferred_subagent_type="screen_review_summary_builder",
        )
        operations: list[SubagentGraphPatchOperation] = []
        candidate_node_ids: list[str] = []
        for index, candidate in enumerate(candidates, start=1):
            node_id = f"screen_review_candidate_{index}"
            candidate_node_ids.append(node_id)
            operations.append(
                SubagentGraphPatchOperation(
                    op_type="insert_subagent",
                    node=self._build_catalog_plan_node(
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
                    ),
                    reason="insert screen candidate review node",
                )
            )
        operations.append(
            SubagentGraphPatchOperation(
                op_type="insert_subagent",
                node=self._build_catalog_plan_node(
                    node_id="screen_review_summary_builder",
                    subagent_type=summary_entry.subagent_type,
                    objective="Aggregate screen-review findings into summary.",
                    inputs={"candidate_count": len(candidates)},
                    depends_on=["screen_review_strategy_resolver", *candidate_node_ids],
                    cache_key="screen_review_summary_builder",
                    notes=["phase=postprocess"],
                ),
                reason="insert screen review summary builder",
            )
        )
        return SubagentGraphPatch(
            policy_source="rule",
            confidence=1.0,
            notes=["insert_screen_review_summary_builder", "insert_screen_review_candidate_nodes" if candidate_node_ids else "no_screen_review_candidate_nodes"],
            operations=operations,
        )

    def _execute_subagent_plan(
        self,
        *,
        plan: SubagentPlan,
        max_workers: int | None,
        dag_id: str,
        build_invocation,
    ) -> dict[str, SubagentDagNodeResult]:
        dag_nodes: list[SubagentDagNodeSpec] = []
        for plan_node in plan.nodes:
            runtime_node = build_invocation[plan_node.node_id]
            dag_nodes.append(
                SubagentDagNodeSpec(
                    node_id=plan_node.node_id,
                    build_invocation=lambda results, *, builder=runtime_node["builder"]: builder(results),
                    depends_on=tuple(plan_node.depends_on),
                    failure_mode=plan_node.failure_mode,
                    allow_partial_dependencies=plan_node.allow_partial_dependencies,
                    max_attempts=plan_node.max_attempts,
                    replayable=plan_node.replayable,
                    cache_scope=plan_node.cache_scope,
                    cache_key=plan_node.cache_key,
                    metadata={
                        **dict(runtime_node["metadata"]),
                        "plan_id": plan.plan_id,
                        "plan_node_id": plan_node.node_id,
                        "subagent_plan_strategy": plan.strategy,
                    },
                )
            )
        return self._execute_subagent_dag(
            dag_nodes,
            max_workers=max_workers,
            dag_id=dag_id,
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

    def _execute_subagent_dag(
        self,
        nodes: list[SubagentDagNodeSpec],
        *,
        max_workers: int | None = None,
        dag_id: str,
        start_from_node_id: str | None = None,
    ) -> dict[str, SubagentDagNodeResult]:
        executor = SubagentDagExecutor(
            invoke_subagent=self._invoke_subagent,
            emit_event=self._emit_runtime_event,
        )
        resolved_start_from_node_id = start_from_node_id or self._subagent_dag_start_from_node_id(
            dag_id=dag_id,
        )
        return executor.execute(
            nodes,
            max_workers=max_workers,
            cache_context=self._subagent_dag_cache_context(dag_id=dag_id),
            start_from_node_id=resolved_start_from_node_id,
        )

    def _dag_metadata(
        self,
        *,
        task_name: str,
        agent_path: str,
        objective: str | None,
    ) -> dict[str, Any]:
        return {
            "task_name": task_name,
            "agent_path": agent_path,
            "objective": objective,
            "phase": "review",
        }

    def _build_subagent_invocation(
        self,
        *,
        subagent_type: str,
        objective: str | None,
        inputs: dict[str, Any],
        task_name: str,
        agent_path: str,
    ) -> SubagentInvocation:
        return SubagentInvocation(
            subagent_type=subagent_type,
            objective=objective,
            inputs=inputs,
            context={
                "task_name": task_name,
                "agent_path": agent_path,
                "phase": "review",
            },
        )

    def _screen_review_trace(self, *, results: dict[str, SubagentDagNodeResult]) -> list[dict[str, Any]]:
        trace: list[dict[str, Any]] = []
        for node_id, result in results.items():
            metadata = dict(result.metadata or {})
            record = {
                "node_id": node_id,
                "subagent_type": str((result.record.subagent_type if result.record is not None else metadata.get("subagent_type")) or node_id),
                "status": result.status,
                "objective": str((result.record.objective if result.record is not None else metadata.get("objective")) or ""),
                "invocation_id": result.invocation_id,
                "task_name": metadata.get("task_name"),
                "agent_path": metadata.get("agent_path"),
                "cache_hit": bool(result.cache_hit),
                "attempts": result.attempts,
            }
            if metadata.get("plan_node_id"):
                record["plan_node_id"] = metadata["plan_node_id"]
            if metadata.get("subagent_plan_strategy"):
                record["subagent_plan_strategy"] = metadata["subagent_plan_strategy"]
            trace.append(record)
        return trace

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
