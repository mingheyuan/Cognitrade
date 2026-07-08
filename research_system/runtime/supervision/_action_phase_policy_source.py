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
        shadow_payload = self.shadow_policy.suggest(
            plan=plan,
            completed_actions=completed_actions,
            pending_actions=pending_actions,
            blackboard_entries=blackboard_entries,
            actual_decision=current_decision,
        )
        final_decision = current_decision

        if isinstance(shadow_payload, dict) and self._llm_enabled():
            llm_decision = self._materialize_policy_decision(
                shadow_payload=shadow_payload,
                pending_actions=pending_actions,
                current_decision=current_decision,
                completed_actions=completed_actions,
                policy_source="llm",
                policy_note="apply_action_phase_llm_suggestion",
                min_confidence=float(getattr(self.settings, "supervisor_llm_policy_min_confidence", 0.7) or 0.7),
            )
            if llm_decision is not None:
                final_decision = llm_decision
                self._record_policy_application(
                    event_name="action_phase_llm_applied",
                    policy_source="llm",
                    decision_store_name="action_phase_llm_decisions",
                    plan=plan,
                    applied_decision=final_decision,
                    shadow_payload=shadow_payload,
                    rule_action=current_decision.next_actions[0] if current_decision.next_actions else None,
                    rule_decision=current_decision,
                )
                self.shadow_policy.observe(
                    plan=plan,
                    completed_actions=completed_actions,
                    pending_actions=pending_actions,
                    blackboard_entries=blackboard_entries,
                    actual_decision=final_decision,
                    shadow_payload=shadow_payload,
                )
                return final_decision

        if isinstance(shadow_payload, dict) and self._hybrid_enabled():
            hybrid_decision = self._materialize_policy_decision(
                shadow_payload=shadow_payload,
                pending_actions=pending_actions,
                current_decision=current_decision,
                completed_actions=completed_actions,
                policy_source="hybrid",
                policy_note="apply_action_phase_hybrid_llm_suggestion",
                min_confidence=float(getattr(self.settings, "supervisor_hybrid_llm_min_confidence", 0.7) or 0.7),
                allow_replan_suppression=False,
            )
            if hybrid_decision is not None:
                final_decision = hybrid_decision
                self._record_policy_application(
                    event_name="action_phase_hybrid_applied",
                    policy_source="llm_hybrid",
                    decision_store_name="action_phase_hybrid_decisions",
                    plan=plan,
                    applied_decision=final_decision,
                    shadow_payload=shadow_payload,
                    rule_action=current_decision.next_actions[0] if current_decision.next_actions else None,
                    rule_decision=current_decision,
                )

        self.shadow_policy.observe(
            plan=plan,
            completed_actions=completed_actions,
            pending_actions=pending_actions,
            blackboard_entries=blackboard_entries,
            actual_decision=final_decision,
            shadow_payload=shadow_payload,
        )
        return final_decision

    def _llm_enabled(self) -> bool:
        return bool(getattr(self.settings, "supervisor_llm_policy_enabled", False))

    def _hybrid_enabled(self) -> bool:
        return bool(getattr(self.settings, "supervisor_hybrid_llm_enabled", False))

    def _materialize_policy_decision(
        self,
        *,
        shadow_payload: dict[str, Any],
        pending_actions: list[ActionSpec],
        current_decision: SupervisorDecision,
        completed_actions: list[str],
        policy_source: str,
        policy_note: str,
        min_confidence: float,
        allow_replan_suppression: bool = True,
    ) -> SupervisorDecision | None:
        confidence = float(shadow_payload.get("confidence") or 0.0)
        if confidence < float(min_confidence):
            return None
        selected_action_node_id = str(shadow_payload.get("selected_action_node_id") or "").strip()
        if not selected_action_node_id:
            return None
        candidate_actions = self._candidate_actions(
            pending_actions=pending_actions,
            current_decision=current_decision,
        )
        selected_action = candidate_actions.get(selected_action_node_id)
        if selected_action is None:
            return None
        selected_requests_replan = bool(shadow_payload.get("request_replan"))
        current_requests_replan = self._decision_requests_replan(current_decision)
        plan_patch = None
        if selected_requests_replan:
            plan_patch = self._build_policy_replan_patch(
                policy_source=policy_source,
                reason=str(shadow_payload.get("reason") or f"{policy_source}_action_phase_replan"),
                confidence=confidence,
                notes=[
                    policy_note,
                    f"{policy_source}_confidence={confidence:.2f}",
                    *list(shadow_payload.get("notes") or []),
                ],
            )
        elif not allow_replan_suppression and current_requests_replan:
            plan_patch = current_decision.plan_patch

        return self.decision_factory.continue_with(
            action=selected_action,
            completed_actions=completed_actions,
            notes=[
                policy_note,
                f"{policy_source}_confidence={confidence:.2f}",
                *list(shadow_payload.get("notes") or []),
            ],
            plan_patch=plan_patch,
        )

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
    def _build_policy_replan_patch(
        *,
        policy_source: str,
        reason: str,
        confidence: float,
        notes: list[str],
    ) -> ActionPlanPatch:
        return ActionPlanPatch(
            decision_type="replan",
            policy_source=policy_source,
            confidence=confidence,
            notes=list(notes),
            operations=[
                ActionPlanPatchOperation(
                    op_type="request_replan",
                    reason=reason,
                    metadata={"policy_source": policy_source},
                )
            ],
            termination_reason=reason,
        )

    def _record_policy_application(
        self,
        *,
        event_name: str,
        policy_source: str,
        decision_store_name: str,
        plan: ActionPlan,
        applied_decision: SupervisorDecision,
        shadow_payload: dict[str, Any],
        rule_action: ActionSpec | None,
        rule_decision: SupervisorDecision,
    ) -> None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or plan.intent or "").strip()
        if context is None or support is None or not request_id or not intent:
            return
        payload = {
            "event": event_name,
            "intent": plan.intent,
            "policy_source": policy_source,
            "selected_action_node_id": shadow_payload.get("selected_action_node_id"),
            "request_replan": bool(shadow_payload.get("request_replan")),
            "reason": shadow_payload.get("reason"),
            "confidence": shadow_payload.get("confidence"),
            "notes": list(shadow_payload.get("notes") or []),
            "candidate_action_node_ids": list(shadow_payload.get("candidate_action_node_ids") or []),
            "rule_action_node_id": rule_action.node_id if rule_action is not None else None,
            "rule_request_replan": self._decision_requests_replan(rule_decision),
            "override_action": bool(
                rule_action is None
                or not applied_decision.next_actions
                or rule_action.node_id != applied_decision.next_actions[0].node_id
            ),
            "override_replan": self._decision_requests_replan(rule_decision) != self._decision_requests_replan(applied_decision),
            "final_action_node_id": applied_decision.next_actions[0].node_id if applied_decision.next_actions else None,
            "final_request_replan": self._decision_requests_replan(applied_decision),
        }
        getattr(context, decision_store_name).append(dict(payload))
        context.dynamic_trace.append(dict(payload))
        support.emit_event(
            context,
            request_id=request_id,
            intent=intent,
            event_type=event_name,
            phase="supervision",
            agent="supervisor",
            message=f"{event_name} applied a model suggestion",
            payload=dict(payload),
        )


__all__ = ["ActionPhaseLLMPolicySource"]
