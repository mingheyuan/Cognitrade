from __future__ import annotations

from collections.abc import Callable
from typing import Any

from research_system.agents._subagent_dag import (
    SubagentDagExecutor,
    SubagentDagNodeResult,
    SubagentDagNodeSpec,
)
from research_system.agents._subagent_phase_shadow_policy import SubagentPhaseLLMShadowPolicy
from research_system.schemas.subagents import (
    SubagentExecutionRecord,
    SubagentCatalogEntry,
    SubagentGraphPatch,
    SubagentGraphPatchOperation,
    SubagentInvocation,
    SubagentPlan,
    SubagentPlanNode,
)
from research_system.subagents.planning import apply_subagent_graph_patch
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
        runtime_binding_getter: Callable[[], dict[str, Any]] | None = None,
        llm_provider=None,
        settings=None,
        service_name: str | None = None,
    ) -> None:
        self.registry = registry
        self.invoke_subagent = invoke_subagent
        self.emit_event = emit_event
        self.cache_context_builder = cache_context_builder
        self.start_from_node_id_builder = start_from_node_id_builder
        self.shadow_policy = SubagentPhaseLLMShadowPolicy(
            llm_provider=llm_provider,
            settings=settings,
            runtime_binding_getter=runtime_binding_getter,
            emit_event=emit_event,
            service_name=service_name,
        )

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

    def apply_patch(
        self,
        *,
        plan: SubagentPlan,
        patch: SubagentGraphPatch | None,
    ) -> tuple[SubagentPlan, list[dict[str, Any]]]:
        if patch is None:
            return plan, []
        updated_plan = apply_subagent_graph_patch(plan, patch)
        return updated_plan, [patch.model_dump(mode="json")]

    def execute_patch_cycle(
        self,
        *,
        plan: SubagentPlan,
        dag_id: str,
        base_max_workers: int | None,
        final_max_workers: int | None,
        build_invocation_factory: Callable[[SubagentPlan], dict[str, dict[str, Any]]],
        patch_builder: Callable[[dict[str, SubagentDagNodeResult]], SubagentGraphPatch | None],
        replan_builder: Callable[[SubagentPlan, SubagentGraphPatch, dict[str, SubagentDagNodeResult]], SubagentPlan] | None = None,
        allow_current_plan_variant: bool = False,
        candidate_plan_variants_builder: Callable[
            [SubagentPlan, SubagentGraphPatch | None, dict[str, SubagentDagNodeResult]],
            dict[str, SubagentPlan],
        ]
        | None = None,
    ) -> tuple[SubagentPlan, dict[str, SubagentDagNodeResult], list[dict[str, Any]], dict[str, SubagentDagNodeResult]]:
        base_results = self.execute_plan(
            plan=plan,
            build_invocation=build_invocation_factory(plan),
            dag_id=dag_id,
            max_workers=base_max_workers,
        )
        patch = patch_builder(base_results)
        rule_variant_id = "current"
        rule_final_plan = plan
        rule_patch_rows: list[dict[str, Any]] = []
        if patch is not None and self._patch_requests_replan(patch):
            if replan_builder is None:
                raise ValueError("subagent patch requested replan but no replan_builder was provided")
            rule_variant_id = "rule_replanned"
            rule_final_plan = replan_builder(plan, patch, base_results)
            rule_patch_rows = [patch.model_dump(mode="json")]
        else:
            rule_final_plan, rule_patch_rows = self.apply_patch(plan=plan, patch=patch)
            if patch is not None and rule_patch_rows:
                rule_variant_id = "rule_patched"
        candidate_plans: dict[str, SubagentPlan] = {}
        if allow_current_plan_variant:
            candidate_plans["current"] = plan
        if patch is not None and rule_variant_id != "current":
            candidate_plans[rule_variant_id] = rule_final_plan
        if candidate_plan_variants_builder is not None:
            for variant_id, variant_plan in dict(
                candidate_plan_variants_builder(rule_final_plan, patch, base_results) or {}
            ).items():
                if isinstance(variant_plan, SubagentPlan):
                    candidate_plans[str(variant_id)] = variant_plan
        rule_variant_id = self._resolve_variant_id(
            target_plan=rule_final_plan,
            candidate_plans=candidate_plans,
            fallback=rule_variant_id,
        )
        shadow_payload = self.shadow_policy.suggest(
            dag_id=dag_id,
            plan=plan,
            candidate_plans=candidate_plans,
            base_results=base_results,
        )
        final_plan = rule_final_plan
        final_patch = patch
        final_patch_rows = list(rule_patch_rows)
        final_plan_variant = rule_variant_id
        final_results = base_results if not final_patch_rows and final_plan is plan else self.execute_plan(
            plan=final_plan,
            build_invocation=build_invocation_factory(final_plan),
            dag_id=dag_id,
            max_workers=final_max_workers,
        )
        if self._hybrid_enabled() and isinstance(shadow_payload, dict):
            hybrid_variant = self._select_hybrid_variant(
                shadow_payload=shadow_payload,
                candidate_plans=candidate_plans,
            )
            if hybrid_variant is not None and hybrid_variant != final_plan_variant:
                if hybrid_variant == "current":
                    final_plan = plan
                    final_patch = None
                    final_patch_rows = []
                    final_plan_variant = "current"
                    final_results = base_results
                else:
                    final_plan = candidate_plans[hybrid_variant]
                    final_patch = self._build_hybrid_patch(
                        variant_id=hybrid_variant,
                        reason=str(shadow_payload.get("reason") or "hybrid_selected_phase_variant"),
                        confidence=float(shadow_payload.get("confidence") or 0.0),
                        notes=[
                            "apply_subagent_phase_hybrid_variant",
                            f"variant={hybrid_variant}",
                            *list(shadow_payload.get("notes") or []),
                        ],
                    )
                    final_patch_rows = [final_patch.model_dump(mode="json")]
                    final_plan_variant = hybrid_variant
                    final_results = self.execute_plan(
                        plan=final_plan,
                        build_invocation=build_invocation_factory(final_plan),
                        dag_id=dag_id,
                        max_workers=final_max_workers,
                    )
                self._record_hybrid_application(
                    dag_id=dag_id,
                    rule_variant_id=rule_variant_id,
                    applied_variant_id=final_plan_variant,
                    shadow_payload=shadow_payload,
                    final_patch=final_patch,
                )
        if isinstance(shadow_payload, dict):
            self.shadow_policy.observe(
                dag_id=dag_id,
                plan=plan,
                candidate_plans=candidate_plans,
                actual_final_plan=final_plan,
                actual_patch=final_patch,
                actual_plan_variant=final_plan_variant,
                base_results=base_results,
                shadow_payload=shadow_payload,
            )
        return final_plan, base_results, final_patch_rows, final_results

    @staticmethod
    def _patch_requests_replan(patch: SubagentGraphPatch) -> bool:
        if patch.decision_type == "replan":
            return True
        return any(operation.op_type == "request_replan" for operation in patch.operations)

    def _hybrid_enabled(self) -> bool:
        settings = getattr(self.shadow_policy, "settings", None)
        return bool(getattr(settings, "subagent_phase_hybrid_llm_enabled", False))

    def _select_hybrid_variant(
        self,
        *,
        shadow_payload: dict[str, Any],
        candidate_plans: dict[str, SubagentPlan],
    ) -> str | None:
        settings = getattr(self.shadow_policy, "settings", None)
        confidence = float(shadow_payload.get("confidence") or 0.0)
        min_confidence = float(getattr(settings, "subagent_phase_hybrid_llm_min_confidence", 0.7) or 0.7)
        if confidence < min_confidence:
            return None
        selected_variant = str(shadow_payload.get("selected_plan_variant") or "").strip()
        if selected_variant and selected_variant in candidate_plans:
            return selected_variant
        return None

    @staticmethod
    def _build_hybrid_patch(
        *,
        variant_id: str,
        reason: str,
        confidence: float,
        notes: list[str],
    ) -> SubagentGraphPatch:
        return SubagentGraphPatch(
            decision_type="replan",
            policy_source="hybrid",
            confidence=confidence,
            notes=list(notes),
            operations=[
                SubagentGraphPatchOperation(
                    op_type="request_replan",
                    reason=reason,
                    metadata={"selected_plan_variant": variant_id, "policy_source": "hybrid"},
                )
            ],
            termination_reason=reason,
        )

    def _record_hybrid_application(
        self,
        *,
        dag_id: str,
        rule_variant_id: str,
        applied_variant_id: str,
        shadow_payload: dict[str, Any],
        final_patch: SubagentGraphPatch | None,
    ) -> None:
        runtime_binding_getter = getattr(self.shadow_policy, "runtime_binding_getter", None)
        binding = dict(runtime_binding_getter() or {}) if callable(runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or shadow_payload.get("intent") or "").strip()
        if context is None or support is None or not request_id or not intent:
            return
        payload = {
            "event": "subagent_phase_hybrid_applied",
            "dag_id": dag_id,
            "intent": shadow_payload.get("intent"),
            "phase": shadow_payload.get("phase"),
            "agent": shadow_payload.get("agent"),
            "policy_source": "llm_hybrid",
            "selected_plan_variant": shadow_payload.get("selected_plan_variant"),
            "selected_node_ids": list(shadow_payload.get("selected_node_ids") or []),
            "reason": shadow_payload.get("reason"),
            "confidence": shadow_payload.get("confidence"),
            "notes": list(shadow_payload.get("notes") or []),
            "request_replan": bool(shadow_payload.get("request_replan")),
            "rule_plan_variant": rule_variant_id,
            "applied_plan_variant": applied_variant_id,
            "override_rule_variant": rule_variant_id != applied_variant_id,
            "final_request_replan": self._patch_requests_replan(final_patch) if final_patch is not None else False,
        }
        context.subagent_phase_hybrid_decisions.append(dict(payload))
        context.dynamic_trace.append(dict(payload))
        if callable(self.emit_event):
            self.emit_event(
                event_type="subagent_phase_hybrid_applied",
                message=f"Hybrid subagent-phase policy applied variant {applied_variant_id} for DAG {dag_id}",
                payload=dict(payload),
                phase=str(shadow_payload.get("phase") or "").strip() or None,
                agent=str(shadow_payload.get("agent") or "").strip() or None,
            )
        else:
            support.emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type="subagent_phase_hybrid_applied",
                phase=str(shadow_payload.get("phase") or "").strip() or None,
                agent=str(shadow_payload.get("agent") or "").strip() or None,
                message=f"Hybrid subagent-phase policy applied variant {applied_variant_id} for DAG {dag_id}",
                payload=dict(payload),
            )

    @staticmethod
    def _resolve_variant_id(
        *,
        target_plan: SubagentPlan,
        candidate_plans: dict[str, SubagentPlan],
        fallback: str,
    ) -> str:
        for variant_id, candidate_plan in candidate_plans.items():
            if candidate_plan is target_plan:
                return variant_id
        return fallback

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
