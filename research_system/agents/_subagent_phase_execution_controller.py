from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from research_system.agents._subagent_dag import SubagentDagNodeResult
from research_system.schemas.subagents import SubagentGraphPatch, SubagentPlan


@dataclass(frozen=True)
class SubagentPhaseExecutionResolution:
    final_plan: SubagentPlan
    base_results: dict[str, SubagentDagNodeResult]
    final_patch_rows: list[dict[str, Any]]
    final_results: dict[str, SubagentDagNodeResult]
    rule_variant_id: str
    final_plan_variant: str
    candidate_plan_variants: list[str]
    final_patch: SubagentGraphPatch | None = None
    shadow_payload: dict[str, Any] | None = None


class SubagentPhaseExecutionController:
    def __init__(
        self,
        *,
        execute_plan: Callable[..., dict[str, SubagentDagNodeResult]],
        apply_patch: Callable[..., tuple[SubagentPlan, list[dict[str, Any]]]],
        llm_policy_source,
        patch_requests_replan,
        resolve_variant_id,
        control_stage_recorder=None,
        runtime_binding_getter=None,
        emit_event=None,
        service_name: str | None = None,
    ) -> None:
        self.execute_plan = execute_plan
        self.apply_patch = apply_patch
        self.llm_policy_source = llm_policy_source
        self.patch_requests_replan = patch_requests_replan
        self.resolve_variant_id = resolve_variant_id
        self.control_stage_recorder = control_stage_recorder
        self.runtime_binding_getter = runtime_binding_getter
        self.emit_event = emit_event
        self.service_name = service_name or "subagent_phase_execution"

    def execute_patch_cycle(
        self,
        *,
        plan: SubagentPlan,
        dag_id: str,
        base_max_workers: int | None,
        final_max_workers: int | None,
        build_invocation_factory: Callable[[SubagentPlan], dict[str, dict[str, Any]]],
        patch_builder: Callable[[dict[str, SubagentDagNodeResult]], SubagentGraphPatch | None],
        replan_builder: Callable[[SubagentPlan, SubagentGraphPatch, dict[str, SubagentDagNodeResult]], SubagentPlan]
        | None = None,
        allow_current_plan_variant: bool = False,
        candidate_plan_variants_builder: Callable[
            [SubagentPlan, SubagentGraphPatch | None, dict[str, SubagentDagNodeResult]],
            dict[str, SubagentPlan],
        ]
        | None = None,
    ) -> SubagentPhaseExecutionResolution:
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
        if patch is not None and self.patch_requests_replan(patch):
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
        rule_variant_id = self.resolve_variant_id(
            target_plan=rule_final_plan,
            candidate_plans=candidate_plans,
            fallback=rule_variant_id,
        )
        final_plan, final_patch, final_patch_rows, final_plan_variant, shadow_payload = self.llm_policy_source.apply(
            dag_id=dag_id,
            plan=plan,
            candidate_plans=candidate_plans,
            base_results=base_results,
            rule_final_plan=rule_final_plan,
            rule_patch=patch,
            rule_patch_rows=rule_patch_rows,
            rule_variant_id=rule_variant_id,
        )
        final_results = (
            base_results
            if not final_patch_rows and final_plan is plan
            else self.execute_plan(
                plan=final_plan,
                build_invocation=build_invocation_factory(final_plan),
                dag_id=dag_id,
                max_workers=final_max_workers,
            )
        )
        self.llm_policy_source.observe(
            dag_id=dag_id,
            plan=plan,
            candidate_plans=candidate_plans,
            actual_final_plan=final_plan,
            actual_patch=final_patch,
            actual_plan_variant=final_plan_variant,
            base_results=base_results,
            shadow_payload=shadow_payload,
        )
        resolution = SubagentPhaseExecutionResolution(
            final_plan=final_plan,
            base_results=base_results,
            final_patch_rows=final_patch_rows,
            final_results=final_results,
            rule_variant_id=rule_variant_id,
            final_plan_variant=final_plan_variant,
            candidate_plan_variants=list(candidate_plans.keys()),
            final_patch=final_patch,
            shadow_payload=shadow_payload,
        )
        self._record_stage_decision(
            dag_id=dag_id,
            plan=plan,
            resolution=resolution,
        )
        return resolution

    def _record_stage_decision(
        self,
        *,
        dag_id: str,
        plan: SubagentPlan,
        resolution: SubagentPhaseExecutionResolution,
    ) -> None:
        binding = (
            dict(self.runtime_binding_getter() or {})
            if callable(self.runtime_binding_getter)
            else {}
        )
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or plan.intent or "").strip()
        phase = str(binding.get("phase") or plan.phase or "").strip() or None
        agent = str(binding.get("agent") or self.service_name).strip() or self.service_name
        if context is None or support is None or not request_id or not intent:
            return
        final_patch = resolution.final_patch
        final_request_replan = (
            self.patch_requests_replan(final_patch) if final_patch is not None else False
        )
        if resolution.final_plan_variant != resolution.rule_variant_id:
            decision_type = "override_rule_variant"
        elif final_request_replan:
            decision_type = "apply_rule_replan"
        elif resolution.final_patch_rows:
            decision_type = "apply_rule_patch"
        else:
            decision_type = "keep_current"
        payload = {
            "event": "subagent_phase_execution_decided",
            "control_stage": "phase_patch_cycle",
            "decision_stage": "phase_execution",
            "dag_id": dag_id,
            "intent": plan.intent,
            "phase": plan.phase,
            "plan_id": plan.plan_id,
            "plan_strategy": plan.strategy,
            "policy_source": str(getattr(final_patch, "policy_source", None) or "rule"),
            "decision_type": decision_type,
            "rule_plan_variant": resolution.rule_variant_id,
            "final_plan_variant": resolution.final_plan_variant,
            "override_rule_variant": resolution.rule_variant_id != resolution.final_plan_variant,
            "candidate_plan_variants": list(resolution.candidate_plan_variants),
            "final_request_replan": final_request_replan,
            "base_node_ids": [node.node_id for node in list(plan.nodes or [])],
            "final_node_ids": [node.node_id for node in list(resolution.final_plan.nodes or [])],
            "patch_count": len(resolution.final_patch_rows),
        }
        control_rows = getattr(context, "subagent_control_stage_decisions", None)
        if not isinstance(control_rows, list):
            try:
                setattr(context, "subagent_control_stage_decisions", [])
            except Exception:
                control_rows = None
            else:
                control_rows = getattr(context, "subagent_control_stage_decisions", None)
        if isinstance(control_rows, list):
            control_rows.append(dict(payload))
        message = (
            f"subagent_phase_execution_decided resolved {decision_type} for DAG {dag_id}"
        )
        if self.control_stage_recorder is not None:
            self.control_stage_recorder.record(
                event_type="subagent_phase_execution_decided",
                message=message,
                payload=dict(payload),
                phase=phase,
                agent=agent,
            )
            return
        dynamic_trace = getattr(context, "dynamic_trace", None)
        if isinstance(dynamic_trace, list):
            dynamic_trace.append(dict(payload))
        if callable(self.emit_event):
            self.emit_event(
                event_type="subagent_phase_execution_decided",
                message=message,
                payload=dict(payload),
                phase=phase,
                agent=agent,
            )
            return
        support_emit_event = getattr(support, "emit_event", None)
        if callable(support_emit_event):
            support_emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type="subagent_phase_execution_decided",
                phase=phase,
                agent=agent,
                message=message,
                payload=dict(payload),
            )


__all__ = [
    "SubagentPhaseExecutionController",
    "SubagentPhaseExecutionResolution",
]
