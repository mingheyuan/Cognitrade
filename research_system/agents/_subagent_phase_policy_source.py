from __future__ import annotations

from collections.abc import Callable
from typing import Any

from research_system.agents._subagent_dag import SubagentDagNodeResult
from research_system.agents._subagent_phase_shadow_policy import SubagentPhaseLLMShadowPolicy
from research_system.schemas.subagents import SubagentGraphPatch, SubagentGraphPatchOperation, SubagentPlan


class SubagentPhaseLLMPolicySource:
    def __init__(
        self,
        *,
        shadow_policy: SubagentPhaseLLMShadowPolicy,
        settings=None,
        runtime_binding_getter: Callable[[], dict[str, Any]] | None = None,
        emit_event: Callable[..., None] | None = None,
    ) -> None:
        self.shadow_policy = shadow_policy
        self.settings = settings
        self.runtime_binding_getter = runtime_binding_getter
        self.emit_event = emit_event

    def apply(
        self,
        *,
        dag_id: str,
        plan: SubagentPlan,
        candidate_plans: dict[str, SubagentPlan],
        base_results: dict[str, SubagentDagNodeResult],
        rule_final_plan: SubagentPlan,
        rule_patch: SubagentGraphPatch | None,
        rule_patch_rows: list[dict[str, Any]],
        rule_variant_id: str,
    ) -> tuple[SubagentPlan, SubagentGraphPatch | None, list[dict[str, Any]], str, dict[str, Any] | None]:
        shadow_payload = self.shadow_policy.suggest(
            dag_id=dag_id,
            plan=plan,
            candidate_plans=candidate_plans,
            base_results=base_results,
        )
        if not isinstance(shadow_payload, dict):
            return rule_final_plan, rule_patch, list(rule_patch_rows), rule_variant_id, shadow_payload

        if self._llm_enabled():
            selected_llm_variant = self._select_policy_variant(
                shadow_payload=shadow_payload,
                candidate_plans=candidate_plans,
                min_confidence=float(getattr(self.settings, "subagent_phase_llm_policy_min_confidence", 0.7) or 0.7),
            )
            if selected_llm_variant is not None:
                final_plan, final_patch, final_patch_rows, final_plan_variant = self._materialize_variant_selection(
                    policy_source="llm",
                    policy_note="apply_subagent_phase_llm_variant",
                    default_reason="llm_selected_phase_variant",
                    selected_variant=selected_llm_variant,
                    plan=plan,
                    candidate_plans=candidate_plans,
                    shadow_payload=shadow_payload,
                )
                self._record_policy_application(
                    event_name="subagent_phase_llm_applied",
                    policy_source="llm",
                    decision_store_name="subagent_phase_llm_decisions",
                    dag_id=dag_id,
                    rule_variant_id=rule_variant_id,
                    applied_variant_id=final_plan_variant,
                    shadow_payload=shadow_payload,
                    final_patch=final_patch,
                )
                return final_plan, final_patch, final_patch_rows, final_plan_variant, shadow_payload

        if not self._hybrid_enabled():
            return rule_final_plan, rule_patch, list(rule_patch_rows), rule_variant_id, shadow_payload

        selected_hybrid_variant = self._select_policy_variant(
            shadow_payload=shadow_payload,
            candidate_plans=candidate_plans,
            min_confidence=float(getattr(self.settings, "subagent_phase_hybrid_llm_min_confidence", 0.7) or 0.7),
        )
        if selected_hybrid_variant is None or selected_hybrid_variant == rule_variant_id:
            return rule_final_plan, rule_patch, list(rule_patch_rows), rule_variant_id, shadow_payload

        final_plan, final_patch, final_patch_rows, final_plan_variant = self._materialize_variant_selection(
            policy_source="hybrid",
            policy_note="apply_subagent_phase_hybrid_variant",
            default_reason="hybrid_selected_phase_variant",
            selected_variant=selected_hybrid_variant,
            plan=plan,
            candidate_plans=candidate_plans,
            shadow_payload=shadow_payload,
        )
        self._record_policy_application(
            event_name="subagent_phase_hybrid_applied",
            policy_source="llm_hybrid",
            decision_store_name="subagent_phase_hybrid_decisions",
            dag_id=dag_id,
            rule_variant_id=rule_variant_id,
            applied_variant_id=final_plan_variant,
            shadow_payload=shadow_payload,
            final_patch=final_patch,
        )
        return final_plan, final_patch, final_patch_rows, final_plan_variant, shadow_payload

    def observe(
        self,
        *,
        dag_id: str,
        plan: SubagentPlan,
        candidate_plans: dict[str, SubagentPlan],
        actual_final_plan: SubagentPlan,
        actual_patch: SubagentGraphPatch | None,
        actual_plan_variant: str | None,
        base_results: dict[str, SubagentDagNodeResult],
        shadow_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(shadow_payload, dict):
            shadow_payload = self.shadow_policy.suggest(
                dag_id=dag_id,
                plan=plan,
                candidate_plans=candidate_plans,
                base_results=base_results,
            )
        if not isinstance(shadow_payload, dict):
            return None
        return self.shadow_policy.observe(
            dag_id=dag_id,
            plan=plan,
            candidate_plans=candidate_plans,
            actual_final_plan=actual_final_plan,
            actual_patch=actual_patch,
            actual_plan_variant=actual_plan_variant,
            base_results=base_results,
            shadow_payload=shadow_payload,
        )

    def _llm_enabled(self) -> bool:
        return bool(getattr(self.settings, "subagent_phase_llm_policy_enabled", False))

    def _hybrid_enabled(self) -> bool:
        return bool(getattr(self.settings, "subagent_phase_hybrid_llm_enabled", False))

    @staticmethod
    def _select_policy_variant(
        *,
        shadow_payload: dict[str, Any],
        candidate_plans: dict[str, SubagentPlan],
        min_confidence: float,
    ) -> str | None:
        confidence = float(shadow_payload.get("confidence") or 0.0)
        if confidence < float(min_confidence):
            return None
        selected_variant = str(shadow_payload.get("selected_plan_variant") or "").strip()
        if selected_variant and selected_variant in candidate_plans:
            return selected_variant
        return None

    def _materialize_variant_selection(
        self,
        *,
        policy_source: str,
        policy_note: str,
        default_reason: str,
        selected_variant: str,
        plan: SubagentPlan,
        candidate_plans: dict[str, SubagentPlan],
        shadow_payload: dict[str, Any],
    ) -> tuple[SubagentPlan, SubagentGraphPatch | None, list[dict[str, Any]], str]:
        if selected_variant == "current":
            return plan, None, [], "current"
        final_plan = candidate_plans[selected_variant]
        final_patch = self._build_policy_patch(
            policy_source=policy_source,
            variant_id=selected_variant,
            reason=str(shadow_payload.get("reason") or default_reason),
            confidence=float(shadow_payload.get("confidence") or 0.0),
            notes=[
                policy_note,
                f"variant={selected_variant}",
                *list(shadow_payload.get("notes") or []),
            ],
        )
        return final_plan, final_patch, [final_patch.model_dump(mode="json")], selected_variant

    @staticmethod
    def _build_policy_patch(
        *,
        policy_source: str,
        variant_id: str,
        reason: str,
        confidence: float,
        notes: list[str],
    ) -> SubagentGraphPatch:
        return SubagentGraphPatch(
            decision_type="replan",
            policy_source=policy_source,
            confidence=confidence,
            notes=list(notes),
            operations=[
                SubagentGraphPatchOperation(
                    op_type="request_replan",
                    reason=reason,
                    metadata={"selected_plan_variant": variant_id, "policy_source": policy_source},
                )
            ],
            termination_reason=reason,
        )

    @staticmethod
    def _patch_requests_replan(patch: SubagentGraphPatch | None) -> bool:
        if patch is None:
            return False
        if patch.decision_type == "replan":
            return True
        return any(operation.op_type == "request_replan" for operation in patch.operations)

    def _record_policy_application(
        self,
        *,
        event_name: str,
        policy_source: str,
        decision_store_name: str,
        dag_id: str,
        rule_variant_id: str,
        applied_variant_id: str,
        shadow_payload: dict[str, Any],
        final_patch: SubagentGraphPatch | None,
    ) -> None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or shadow_payload.get("intent") or "").strip()
        if context is None or support is None or not request_id or not intent:
            return
        payload = {
            "event": event_name,
            "dag_id": dag_id,
            "intent": shadow_payload.get("intent"),
            "phase": shadow_payload.get("phase"),
            "agent": shadow_payload.get("agent"),
            "policy_source": policy_source,
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
        getattr(context, decision_store_name).append(dict(payload))
        context.dynamic_trace.append(dict(payload))
        if callable(self.emit_event):
            self.emit_event(
                event_type=event_name,
                message=f"{event_name} selected variant {applied_variant_id} for DAG {dag_id}",
                payload=dict(payload),
                phase=str(shadow_payload.get("phase") or "").strip() or None,
                agent=str(shadow_payload.get("agent") or "").strip() or None,
            )
        else:
            support.emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type=event_name,
                phase=str(shadow_payload.get("phase") or "").strip() or None,
                agent=str(shadow_payload.get("agent") or "").strip() or None,
                message=f"{event_name} selected variant {applied_variant_id} for DAG {dag_id}",
                payload=dict(payload),
            )


__all__ = ["SubagentPhaseLLMPolicySource"]
