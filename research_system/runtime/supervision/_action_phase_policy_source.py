from __future__ import annotations

from typing import Any, Callable

from research_system.runtime.supervision._action_phase_shadow_policy import ActionPhaseLLMShadowPolicy
from research_system.schemas.planning import ActionPlan, ActionPlanPatch, ActionPlanPatchOperation, ActionSpec, SupervisorDecision


class ActionPhaseLLMPolicySource:
    def __init__(
        self,
        *,
        shadow_policy: ActionPhaseLLMShadowPolicy,
        decision_factory,
        settings=None,
        runtime_binding_getter: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.shadow_policy = shadow_policy
        self.decision_factory = decision_factory
        self.settings = settings
        self.runtime_binding_getter = runtime_binding_getter

    def bind_runtime(self, *, context, support, request_id: str, intent: str, execution_policy=None) -> None:
        self.shadow_policy.bind_runtime(
            context=context,
            support=support,
            request_id=request_id,
            intent=intent,
            execution_policy=execution_policy,
        )

    def clear_runtime(self) -> None:
        self.shadow_policy.clear_runtime()

    def apply(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries,
        current_decision: SupervisorDecision,
    ) -> SupervisorDecision:
        shadow_payload = self.shadow_policy.observe(
            plan=plan,
            completed_actions=completed_actions,
            pending_actions=pending_actions,
            blackboard_entries=blackboard_entries,
            actual_decision=current_decision,
        )
        if not self._hybrid_enabled():
            return current_decision
        if not isinstance(shadow_payload, dict):
            return current_decision
        confidence = float(shadow_payload.get("confidence") or 0.0)
        min_confidence = float(getattr(self.settings, "supervisor_hybrid_llm_min_confidence", 0.7) or 0.7)
        if confidence < min_confidence:
            return current_decision
        selected_action_node_id = str(shadow_payload.get("selected_action_node_id") or "").strip()
        if not selected_action_node_id:
            return current_decision
        candidate_actions = self._candidate_actions(
            pending_actions=pending_actions,
            current_decision=current_decision,
        )
        selected_action = candidate_actions.get(selected_action_node_id)
        if selected_action is None:
            return current_decision
        current_action = current_decision.next_actions[0] if current_decision.next_actions else None
        current_requests_replan = self._decision_requests_replan(current_decision)
        selected_requests_replan = bool(shadow_payload.get("request_replan"))
        override_action = current_action is None or current_action.node_id != selected_action.node_id
        override_replan = selected_requests_replan and not current_requests_replan
        if not override_action and not override_replan:
            return current_decision
        plan_patch = current_decision.plan_patch
        hybrid_notes = [
            "apply_action_phase_hybrid_llm_suggestion",
            f"hybrid_confidence={confidence:.2f}",
            *list(shadow_payload.get("notes") or []),
        ]
        if override_replan:
            plan_patch = self._build_hybrid_replan_patch(
                reason=str(shadow_payload.get("reason") or "hybrid_action_phase_replan"),
                confidence=confidence,
                notes=hybrid_notes,
            )
        decision = self.decision_factory.continue_with(
            action=selected_action,
            completed_actions=completed_actions,
            notes=hybrid_notes,
            plan_patch=plan_patch,
        )
        self._record_hybrid_application(
            plan=plan,
            applied_decision=decision,
            shadow_payload=shadow_payload,
            override_action=override_action,
            override_replan=override_replan,
            rule_action=current_action,
        )
        return decision

    def _hybrid_enabled(self) -> bool:
        return bool(getattr(self.settings, "supervisor_hybrid_llm_enabled", False))

    def _candidate_actions(
        self,
        *,
        pending_actions: list[ActionSpec],
        current_decision: SupervisorDecision,
    ) -> dict[str, ActionSpec]:
        indexed = self.decision_factory.pending_by_id(pending_actions)
        for action in list(current_decision.next_actions or []):
            indexed.setdefault(action.node_id, action)
        return indexed

    @staticmethod
    def _decision_requests_replan(decision: SupervisorDecision) -> bool:
        patch = decision.plan_patch
        if patch is None:
            return False
        if patch.decision_type == "replan":
            return True
        return any(operation.op_type == "request_replan" for operation in patch.operations)

    @staticmethod
    def _build_hybrid_replan_patch(
        *,
        reason: str,
        confidence: float,
        notes: list[str],
    ) -> ActionPlanPatch:
        return ActionPlanPatch(
            decision_type="replan",
            policy_source="hybrid",
            confidence=confidence,
            notes=list(notes),
            operations=[
                ActionPlanPatchOperation(
                    op_type="request_replan",
                    reason=reason,
                    metadata={"policy_source": "hybrid"},
                )
            ],
            termination_reason=reason,
        )

    def _record_hybrid_application(
        self,
        *,
        plan: ActionPlan,
        applied_decision: SupervisorDecision,
        shadow_payload: dict[str, Any],
        override_action: bool,
        override_replan: bool,
        rule_action: ActionSpec | None,
    ) -> None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or plan.intent or "").strip()
        if context is None or support is None or not request_id or not intent:
            return
        payload = {
            "event": "action_phase_hybrid_applied",
            "intent": plan.intent,
            "policy_source": "llm_hybrid",
            "selected_action_node_id": shadow_payload.get("selected_action_node_id"),
            "request_replan": bool(shadow_payload.get("request_replan")),
            "reason": shadow_payload.get("reason"),
            "confidence": shadow_payload.get("confidence"),
            "notes": list(shadow_payload.get("notes") or []),
            "candidate_action_node_ids": list(shadow_payload.get("candidate_action_node_ids") or []),
            "rule_action_node_id": rule_action.node_id if rule_action is not None else None,
            "rule_request_replan": bool(shadow_payload.get("actual_request_replan")),
            "override_action": bool(override_action),
            "override_replan": bool(override_replan),
            "final_action_node_id": applied_decision.next_actions[0].node_id if applied_decision.next_actions else None,
            "final_request_replan": self._decision_requests_replan(applied_decision),
        }
        context.action_phase_hybrid_decisions.append(dict(payload))
        context.dynamic_trace.append(dict(payload))
        support.emit_event(
            context,
            request_id=request_id,
            intent=intent,
            event_type="action_phase_hybrid_applied",
            phase="supervision",
            agent="supervisor",
            message="Hybrid action-phase policy applied a model suggestion",
            payload=dict(payload),
        )


__all__ = ["ActionPhaseLLMPolicySource"]
