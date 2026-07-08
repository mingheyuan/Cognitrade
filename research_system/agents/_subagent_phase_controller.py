from __future__ import annotations

from collections.abc import Callable
from typing import Any

from research_system.agents._subagent_dag import (
    SubagentDagExecutor,
    SubagentDagNodeResult,
    SubagentDagNodeSpec,
)
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentInvocation,
    SubagentPlan,
    SubagentPlanNode,
)
from research_system.subagents.registry import SubagentRegistry


class SubagentPhaseController:
    def __init__(
        self,
        *,
        registry: SubagentRegistry,
        invoke_subagent: Callable[[SubagentInvocation], tuple[dict[str, Any], Any]],
        emit_event: Callable[..., None] | None,
        cache_context_builder: Callable[..., Any],
        start_from_node_id_builder: Callable[..., str | None],
    ) -> None:
        self.registry = registry
        self.invoke_subagent = invoke_subagent
        self.emit_event = emit_event
        self.cache_context_builder = cache_context_builder
        self.start_from_node_id_builder = start_from_node_id_builder

    def select_catalog_entry(
        self,
        *,
        stage: str,
        intent: str,
        capability: str,
        preferred_subagent_type: str | None = None,
        requires_llm: bool | None = None,
    ) -> SubagentCatalogEntry:
        candidates = self.registry.catalog_entries(
            stage=stage,
            intent=intent,
            capability=capability,
            requires_llm=requires_llm,
        )
        if not candidates:
            candidates = self.registry.catalog_entries(
                stage=stage,
                capability=capability,
                requires_llm=requires_llm,
            )
        if not candidates:
            raise KeyError(
                f"no catalog entry for stage={stage!r} intent={intent!r} capability={capability!r}"
            )
        if preferred_subagent_type:
            for candidate in candidates:
                if candidate.subagent_type == preferred_subagent_type:
                    return candidate
        return sorted(
            candidates,
            key=lambda candidate: (
                bool(candidate.requires_llm),
                self._cost_rank(candidate.cost_class),
                self._latency_rank(candidate.latency_class),
                candidate.subagent_type,
            ),
        )[0]

    def build_catalog_plan_node(
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
        return SubagentPlanNode(
            node_id=node_id,
            subagent_type=subagent_type,
            objective=objective,
            inputs=dict(inputs),
            depends_on=list(depends_on or []),
            tags=list(tags or ["catalog_selected"]),
            failure_mode=failure_mode,  # type: ignore[arg-type]
            allow_partial_dependencies=allow_partial_dependencies,
            max_attempts=max_attempts,
            replayable=replayable,
            cache_scope=cache_scope,  # type: ignore[arg-type]
            cache_key=cache_key,
            notes=list(notes or []),
        )

    def execute_plan(
        self,
        *,
        plan: SubagentPlan,
        build_invocation: dict[str, dict[str, Any]],
        dag_id: str,
        max_workers: int | None = None,
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
        executor = SubagentDagExecutor(
            invoke_subagent=self.invoke_subagent,
            emit_event=self.emit_event,
        )
        resolved_start_from_node_id = self.start_from_node_id_builder(dag_id=dag_id)
        return executor.execute(
            dag_nodes,
            max_workers=max_workers,
            cache_context=self.cache_context_builder(dag_id=dag_id),
            start_from_node_id=resolved_start_from_node_id,
        )

    def build_metadata(
        self,
        *,
        task_name: str,
        agent_path: str,
        objective: str | None,
        phase: str,
        extras: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_name": task_name,
            "agent_path": agent_path,
            "objective": objective,
            "phase": phase,
        }
        payload.update(dict(extras or {}))
        return payload

    def build_invocation(
        self,
        *,
        subagent_type: str,
        objective: str | None,
        inputs: dict[str, Any],
        task_name: str,
        agent_path: str,
        phase: str,
        extra_context: dict[str, Any] | None = None,
    ) -> SubagentInvocation:
        context = {
            "task_name": task_name,
            "agent_path": agent_path,
            "phase": phase,
        }
        context.update(dict(extra_context or {}))
        return SubagentInvocation(
            subagent_type=subagent_type,
            objective=objective,
            inputs=inputs,
            context=context,
        )

    def trace_from_results(
        self,
        *,
        results: dict[str, SubagentDagNodeResult],
    ) -> list[dict[str, Any]]:
        trace: list[dict[str, Any]] = []
        for node_id, result in results.items():
            metadata = dict(result.metadata or {})
            record = {
                "node_id": node_id,
                "subagent_type": str(
                    (result.record.subagent_type if result.record is not None else metadata.get("subagent_type"))
                    or node_id
                ),
                "status": result.status,
                "objective": str(
                    (result.record.objective if result.record is not None else metadata.get("objective")) or ""
                ),
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
            if result.record is not None and result.record.degrade_reason:
                record["degrade_reason"] = result.record.degrade_reason
            if result.blocked_by:
                record["blocked_by"] = list(result.blocked_by)
            if result.retry_errors:
                record["retry_errors"] = list(result.retry_errors)
            trace.append(record)
        return trace

    @staticmethod
    def _cost_rank(cost_class: str | None) -> int:
        return {"low": 0, "medium": 1, "high": 2}.get(str(cost_class or "").strip().lower(), 3)

    @staticmethod
    def _latency_rank(latency_class: str | None) -> int:
        return {"low": 0, "medium": 1, "high": 2}.get(str(latency_class or "").strip().lower(), 3)


__all__ = ["SubagentPhaseController"]
