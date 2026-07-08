from __future__ import annotations

from research_system.schemas.planning import ActionPlanPatch, ActionPlanPatchOperation
from research_system.runtime.supervision._action_phase_controller import ActionPhaseController
from research_system.runtime.supervision._action_phase_escalation import (
    ReviewThesisEscalationPolicy,
    SectorResearchEscalationPolicy,
    StockCheckEscalationPolicy,
    StockScreenEscalationPolicy,
)
from research_system.runtime.supervision._action_phase_shadow_policy import ActionPhaseLLMShadowPolicy
from research_system.runtime.supervision.blackboard_view import SupervisorBlackboardView
from research_system.runtime.supervision.decision_factory import SupervisorDecisionFactory
from research_system.runtime.supervision.review_thesis_support import SupervisorReviewThesisSupport
from research_system.runtime.supervision.sector_research_support import SupervisorSectorResearchSupport
from research_system.runtime.supervision.stock_check_support import SupervisorStockCheckSupport
from research_system.runtime.supervision.stock_screen_support import SupervisorStockScreenSupport
from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class RuntimeSupervisor:
    def __init__(self, llm_provider=None, settings=None) -> None:
        self.llm_provider = llm_provider
        self.settings = settings
        self._runtime_binding: dict[str, object] | None = None
        self.blackboard_view = SupervisorBlackboardView()
        self.decision_factory = SupervisorDecisionFactory()
        self.stock_screen = SupervisorStockScreenSupport(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.stock_check = SupervisorStockCheckSupport(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.sector_research = SupervisorSectorResearchSupport(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.review_thesis = SupervisorReviewThesisSupport(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.stock_screen_escalation = StockScreenEscalationPolicy(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.stock_check_escalation = StockCheckEscalationPolicy(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.sector_research_escalation = SectorResearchEscalationPolicy(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.review_thesis_escalation = ReviewThesisEscalationPolicy(
            blackboard_view=self.blackboard_view,
            decision_factory=self.decision_factory,
        )
        self.phase_controller = ActionPhaseController(
            policies_by_intent={
                "stock_screen": self.stock_screen,
                "stock_check": self.stock_check,
                "sector_research": self.sector_research,
                "review_thesis": self.review_thesis,
            },
            escalation_policies_by_intent={
                "stock_screen": self.stock_screen_escalation,
                "stock_check": self.stock_check_escalation,
                "sector_research": self.sector_research_escalation,
                "review_thesis": self.review_thesis_escalation,
            },
            decision_factory=self.decision_factory,
        )
        self.shadow_policy = ActionPhaseLLMShadowPolicy(
            llm_provider=self.llm_provider,
            settings=self.settings,
            blackboard_view=self.blackboard_view,
        )

    def decide(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        decision = self.phase_controller.decide(
            plan=plan,
            completed_actions=completed_actions,
            pending_actions=pending_actions,
            blackboard_entries=blackboard_entries,
        )
        shadow_payload = self.shadow_policy.observe(
            plan=plan,
            completed_actions=completed_actions,
            pending_actions=pending_actions,
            blackboard_entries=blackboard_entries,
            actual_decision=decision,
        )
        decision = self._maybe_apply_hybrid_decision(
            plan=plan,
            completed_actions=completed_actions,
            pending_actions=pending_actions,
            current_decision=decision,
            shadow_payload=shadow_payload,
        )
        return decision

    def bind_runtime(
        self,
        *,
        context,
        support,
        request_id: str,
        intent: str,
    ) -> None:
        self._runtime_binding = {
            "context": context,
            "support": support,
            "request_id": request_id,
            "intent": intent,
        }
        self.shadow_policy.bind_runtime(
            context=context,
            support=support,
            request_id=request_id,
            intent=intent,
        )

    def clear_runtime(self) -> None:
        self._runtime_binding = None
        self.shadow_policy.clear_runtime()

    def _maybe_apply_hybrid_decision(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        current_decision: SupervisorDecision,
        shadow_payload: dict | None,
    ) -> SupervisorDecision:
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
        patch = ActionPlanPatch(
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
        return patch

    def _record_hybrid_application(
        self,
        *,
        plan: ActionPlan,
        applied_decision: SupervisorDecision,
        shadow_payload: dict,
        override_action: bool,
        override_replan: bool,
        rule_action: ActionSpec | None,
    ) -> None:
        binding = dict(self._runtime_binding or {})
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
