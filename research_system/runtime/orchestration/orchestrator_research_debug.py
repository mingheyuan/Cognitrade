from __future__ import annotations

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.planning import ActionPlan
from research_system.schemas.protocol import RequestEnvelope


class OrchestratorDebugSupport:
    def build_debug_payload(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        plan_intent: str,
        retrieval_summary: dict | None,
    ) -> dict:
        request_dispatch_trace: dict | None = None
        if isinstance(request.constraints, dict):
            routing_trace = request.constraints.get("routing_trace")
            chat_dispatch = request.constraints.get("chat_dispatch")
            if isinstance(routing_trace, dict):
                request_dispatch_trace = dict(routing_trace)
            if isinstance(chat_dispatch, dict):
                request_dispatch_trace = {**(request_dispatch_trace or {}), **dict(chat_dispatch)}
        return {
            "intent": plan_intent,
            "dispatch_trace": request_dispatch_trace or dict(plan.dispatch_trace or {}),
            "entry_strategy": plan.entry_strategy,
            "entry_reason": plan.entry_reason,
            "subagent_required": plan.subagent_required,
            "subagent_trace": context.subagent_trace[:],
            "subagent_plans": context.subagent_plans[:],
            "subagent_plan_patches": context.subagent_plan_patches[:],
            "subagent_capability_gaps": context.subagent_capability_gaps[:],
            "subagent_tool_discovery_requests": context.subagent_tool_discovery_requests[:],
            "subagent_discovery_stage_decisions": context.subagent_discovery_stage_decisions[:],
            "subagent_discovery_shadow_suggestions": context.subagent_discovery_shadow_suggestions[:],
            "subagent_discovery_llm_decisions": context.subagent_discovery_llm_decisions[:],
            "subagent_discovery_hybrid_decisions": context.subagent_discovery_hybrid_decisions[:],
            "subagent_discovery_candidate_shadow_suggestions": context.subagent_discovery_candidate_shadow_suggestions[:],
            "subagent_discovery_candidate_llm_decisions": context.subagent_discovery_candidate_llm_decisions[:],
            "subagent_discovery_candidate_hybrid_decisions": context.subagent_discovery_candidate_hybrid_decisions[:],
            "subagent_phase_shadow_suggestions": context.subagent_phase_shadow_suggestions[:],
            "subagent_phase_llm_decisions": context.subagent_phase_llm_decisions[:],
            "subagent_phase_hybrid_decisions": context.subagent_phase_hybrid_decisions[:],
            "action_plan_patches": context.action_plan_patches[:],
            "action_phase_shadow_suggestions": context.action_phase_shadow_suggestions[:],
            "action_phase_llm_decisions": context.action_phase_llm_decisions[:],
            "action_phase_hybrid_decisions": context.action_phase_hybrid_decisions[:],
            "dynamic_trace": context.dynamic_trace[:],
            "retrieval_summary": retrieval_summary,
        }
