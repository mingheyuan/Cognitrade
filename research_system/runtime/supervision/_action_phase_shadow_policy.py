from __future__ import annotations

from dataclasses import asdict

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.core.execution_policy import derive_llm_error_reason
from research_system.runtime.supervision.blackboard_models import (
    ReviewThesisView,
    SectorResearchView,
    StockCheckView,
    StockScreenView,
)
from research_system.runtime.providers.provider_registry import LLMProviderError
from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class ActionPhaseShadowSuggestionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_action_node_id: str | None = None
    request_replan: bool = False
    reason: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)


class ActionPhaseLLMShadowPolicy:
    def __init__(self, *, llm_provider=None, settings=None, blackboard_view=None) -> None:
        self.llm_provider = llm_provider
        self.settings = settings
        self.blackboard_view = blackboard_view
        self._runtime_binding: dict[str, object] | None = None

    def bind_runtime(self, *, context, support, request_id: str, intent: str) -> None:
        self._runtime_binding = {
            "context": context,
            "support": support,
            "request_id": request_id,
            "intent": intent,
        }

    def clear_runtime(self) -> None:
        self._runtime_binding = None

    def observe(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
        actual_decision: SupervisorDecision,
    ) -> dict | None:
        runtime_binding = dict(self._runtime_binding or {})
        context = runtime_binding.get("context")
        support = runtime_binding.get("support")
        request_id = str(runtime_binding.get("request_id") or "")
        if context is None or support is None or not request_id:
            return None
        if not self._enabled():
            return None
        llm_decision = support.resolve_llm_execution(
            context,
            request_id=request_id,
            agent="supervisor",
            phase="supervision",
            strategy="action_phase_shadow_policy",
            provider=self.llm_provider,
            reserve=True,
        )
        if not llm_decision.can_use_llm:
            return None
        candidate_actions = self._candidate_actions(
            pending_actions=pending_actions,
            actual_decision=actual_decision,
        )
        if not candidate_actions and actual_decision.decision_type == "stop":
            candidate_actions = []
        prompt_payload = {
            "intent": plan.intent,
            "completed_actions": list(completed_actions),
            "ready_actions": [self._action_summary(action) for action in pending_actions],
            "candidate_actions": [self._action_summary(action) for action in candidate_actions],
            "blackboard_view": self._blackboard_summary(plan.intent, blackboard_entries),
        }
        try:
            suggestion = self.llm_provider.parse(
                system_prompt=(
                    "You are the shadow action-phase policy for a controlled research orchestrator.\n"
                    "You do not execute actions. Choose the best next action from the provided candidate_actions list and decide whether the action graph should request a replan.\n"
                    "Never invent node ids outside candidate_actions.\n"
                    "If no action should run, selected_action_node_id can be null."
                ),
                user_prompt=(
                    f"Current action-phase state: {prompt_payload}\n"
                    "Return a compact structured suggestion."
                ),
                schema=ActionPhaseShadowSuggestionPayload,
                temperature=0.0,
                reasoning_effort=str(getattr(self.settings, "supervisor_shadow_llm_reasoning_effort", "low") or "low"),
            )
        except LLMProviderError as exc:
            support.record_degradation(
                context,
                request_id=request_id,
                component="action_phase_shadow_policy",
                reason=derive_llm_error_reason(exc),
                phase="supervision",
                agent="supervisor",
                payload={"intent": plan.intent, "strategy": "action_phase_shadow_policy"},
            )
            return None
        actual_action = actual_decision.next_actions[0] if actual_decision.next_actions else None
        actual_request_replan = self._decision_requests_replan(actual_decision)
        payload = {
            "event": "action_phase_shadow_suggested",
            "intent": plan.intent,
            "policy_source": "llm_shadow",
            "selected_action_node_id": suggestion.selected_action_node_id,
            "request_replan": bool(suggestion.request_replan),
            "reason": suggestion.reason,
            "confidence": suggestion.confidence,
            "notes": list(suggestion.notes or []),
            "actual_action_node_id": actual_action.node_id if actual_action is not None else None,
            "actual_request_replan": actual_request_replan,
            "action_match": (
                suggestion.selected_action_node_id == actual_action.node_id
                if actual_action is not None and suggestion.selected_action_node_id
                else suggestion.selected_action_node_id is None and actual_action is None
            ),
            "replan_match": bool(suggestion.request_replan) == actual_request_replan,
            "candidate_action_node_ids": [action.node_id for action in candidate_actions],
            "completed_actions": list(completed_actions),
            "blackboard_view": prompt_payload["blackboard_view"],
        }
        context.action_phase_shadow_suggestions.append(dict(payload))
        context.dynamic_trace.append(dict(payload))
        support.emit_event(
            context,
            request_id=request_id,
            intent=plan.intent,
            event_type="action_phase_shadow_suggested",
            phase="supervision",
            agent="supervisor",
            message="Shadow action-phase policy evaluated the current frontier",
            payload=dict(payload),
        )
        return payload

    def _enabled(self) -> bool:
        if self.llm_provider is None or self.blackboard_view is None:
            return False
        if not bool(getattr(self.llm_provider, "available", True)):
            return False
        if not bool(getattr(self.settings, "supervisor_shadow_llm_enabled", False)):
            return False
        max_llm_calls_per_turn = int(getattr(self.settings, "max_llm_calls_per_turn", 1) or 1)
        return max_llm_calls_per_turn > 1

    @staticmethod
    def _candidate_actions(
        *,
        pending_actions: list[ActionSpec],
        actual_decision: SupervisorDecision,
    ) -> list[ActionSpec]:
        indexed: dict[str, ActionSpec] = {}
        for action in pending_actions + list(actual_decision.next_actions or []):
            indexed.setdefault(action.node_id, action)
        return list(indexed.values())

    @staticmethod
    def _action_summary(action: ActionSpec) -> dict[str, object]:
        return {
            "node_id": action.node_id,
            "phase": action.phase,
            "agent": action.agent,
            "depends_on": list(action.depends_on),
            "tags": list(action.tags),
        }

    def _blackboard_summary(self, intent: str, blackboard_entries: list[BlackboardEntry]) -> dict[str, object]:
        if intent == "stock_screen":
            return asdict(self.blackboard_view.stock_screen_view(blackboard_entries))
        if intent == "stock_check":
            return asdict(self.blackboard_view.stock_check_view(blackboard_entries))
        if intent == "sector_research":
            return asdict(self.blackboard_view.sector_research_view(blackboard_entries))
        if intent == "review_thesis":
            return asdict(self.blackboard_view.review_thesis_view(blackboard_entries))
        return {"entry_count": len(blackboard_entries)}

    @staticmethod
    def _decision_requests_replan(decision: SupervisorDecision) -> bool:
        patch = decision.plan_patch
        if patch is None:
            return False
        if patch.decision_type == "replan":
            return True
        return any(operation.op_type == "request_replan" for operation in patch.operations)


__all__ = ["ActionPhaseLLMShadowPolicy", "ActionPhaseShadowSuggestionPayload"]
