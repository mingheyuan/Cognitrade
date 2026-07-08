from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from research_system.agents._subagent_dag import SubagentDagNodeResult
from research_system.runtime.core.execution_policy import derive_llm_error_reason
from research_system.runtime.providers.provider_registry import LLMProviderError
from research_system.schemas.subagents import SubagentGraphPatch, SubagentPlan, SubagentPlanNode


class SubagentPhaseShadowSuggestionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_node_ids: list[str] = Field(default_factory=list)
    request_replan: bool = False
    reason: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)


class SubagentPhaseLLMShadowPolicy:
    def __init__(
        self,
        *,
        llm_provider=None,
        settings=None,
        runtime_binding_getter=None,
        emit_event=None,
        service_name: str | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.settings = settings
        self.runtime_binding_getter = runtime_binding_getter
        self.emit_event = emit_event
        self.service_name = service_name or "subagent_service"

    def observe(
        self,
        *,
        dag_id: str,
        plan: SubagentPlan,
        final_plan: SubagentPlan,
        patch: SubagentGraphPatch | None,
        base_results: dict[str, SubagentDagNodeResult],
    ) -> dict[str, Any] | None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or getattr(plan, "intent", "") or "").strip()
        phase = str(binding.get("phase") or getattr(plan, "phase", "") or dag_id).strip() or dag_id
        agent = str(binding.get("agent") or self.service_name).strip() or self.service_name
        if context is None or support is None or not request_id or not intent:
            return None
        if not self._enabled(binding=binding):
            return None
        llm_decision = support.resolve_llm_execution(
            context,
            request_id=request_id,
            agent=agent,
            phase=phase,
            strategy="subagent_phase_shadow_policy",
            provider=self.llm_provider,
            reserve=True,
        )
        if not llm_decision.can_use_llm:
            return None
        candidate_nodes = self._candidate_nodes(plan=plan, final_plan=final_plan)
        prompt_payload = {
            "intent": intent,
            "phase": phase,
            "dag_id": dag_id,
            "plan_strategy": plan.strategy,
            "current_plan_nodes": [self._plan_node_summary(node) for node in plan.nodes],
            "candidate_plan_nodes": [self._plan_node_summary(node) for node in candidate_nodes],
            "base_result_summary": [self._result_summary(node_id=node_id, result=result) for node_id, result in base_results.items()],
        }
        try:
            suggestion = self.llm_provider.parse(
                system_prompt=(
                    "You are the shadow phase-policy for a controlled subagent DAG orchestrator.\n"
                    "You do not execute or mutate the DAG.\n"
                    "Decide whether the phase should request a replan and which node ids should appear in the working plan after this control step.\n"
                    "selected_node_ids must be chosen only from candidate_plan_nodes.\n"
                    "Prefer compact, conservative suggestions."
                ),
                user_prompt=(
                    f"Current subagent phase state: {prompt_payload}\n"
                    "Return a compact structured suggestion."
                ),
                schema=SubagentPhaseShadowSuggestionPayload,
                temperature=0.0,
                reasoning_effort=str(
                    getattr(self.settings, "subagent_phase_shadow_llm_reasoning_effort", "low") or "low"
                ),
            )
        except LLMProviderError as exc:
            support.record_degradation(
                context,
                request_id=request_id,
                component="subagent_phase_shadow_policy",
                reason=derive_llm_error_reason(exc),
                phase=phase,
                agent=agent,
                payload={"intent": intent, "dag_id": dag_id, "strategy": "subagent_phase_shadow_policy"},
            )
            return None
        except Exception as exc:
            support.record_degradation(
                context,
                request_id=request_id,
                component="subagent_phase_shadow_policy",
                reason=derive_llm_error_reason(exc),
                phase=phase,
                agent=agent,
                payload={"intent": intent, "dag_id": dag_id, "strategy": "subagent_phase_shadow_policy"},
            )
            return None
        candidate_node_ids = [node.node_id for node in candidate_nodes]
        selected_node_ids = [
            node_id
            for node_id in list(suggestion.selected_node_ids or [])
            if node_id in candidate_node_ids
        ]
        actual_final_node_ids = [node.node_id for node in final_plan.nodes]
        actual_request_replan = self._patch_requests_replan(patch)
        payload = {
            "event": "subagent_phase_shadow_suggested",
            "policy_source": "llm_shadow",
            "intent": intent,
            "phase": phase,
            "agent": agent,
            "dag_id": dag_id,
            "plan_id": plan.plan_id,
            "final_plan_id": final_plan.plan_id,
            "selected_node_ids": selected_node_ids,
            "request_replan": bool(suggestion.request_replan),
            "reason": suggestion.reason,
            "confidence": suggestion.confidence,
            "notes": list(suggestion.notes or []),
            "candidate_node_ids": candidate_node_ids,
            "actual_final_node_ids": actual_final_node_ids,
            "actual_request_replan": actual_request_replan,
            "plan_match": set(selected_node_ids) == set(actual_final_node_ids),
            "replan_match": bool(suggestion.request_replan) == actual_request_replan,
        }
        context.subagent_phase_shadow_suggestions.append(dict(payload))
        context.dynamic_trace.append(dict(payload))
        if callable(self.emit_event):
            self.emit_event(
                event_type="subagent_phase_shadow_suggested",
                message=f"Shadow subagent-phase policy evaluated DAG {dag_id}",
                payload=dict(payload),
                phase=phase,
                agent=agent,
            )
        return payload

    def _enabled(self, *, binding: dict[str, Any]) -> bool:
        if self.llm_provider is None:
            return False
        if not bool(getattr(self.llm_provider, "available", True)):
            return False
        if not bool(getattr(self.settings, "subagent_phase_shadow_llm_enabled", False)):
            return False
        execution_policy = binding.get("execution_policy")
        max_llm_calls_per_turn = int(
            getattr(
                execution_policy,
                "max_llm_calls_per_turn",
                getattr(self.settings, "max_llm_calls_per_turn", 1),
            )
            or 1
        )
        return max_llm_calls_per_turn > 1

    @staticmethod
    def _candidate_nodes(*, plan: SubagentPlan, final_plan: SubagentPlan) -> list[SubagentPlanNode]:
        indexed: dict[str, SubagentPlanNode] = {}
        for node in list(plan.nodes) + list(final_plan.nodes):
            indexed.setdefault(node.node_id, node)
        return list(indexed.values())

    @staticmethod
    def _plan_node_summary(node: SubagentPlanNode) -> dict[str, Any]:
        return {
            "node_id": node.node_id,
            "subagent_type": node.subagent_type,
            "depends_on": list(node.depends_on),
            "tags": list(node.tags),
            "notes": list(node.notes),
        }

    @classmethod
    def _result_summary(cls, *, node_id: str, result: SubagentDagNodeResult) -> dict[str, Any]:
        outputs = dict(result.outputs or {})
        record = result.record
        return {
            "node_id": node_id,
            "status": result.status,
            "cache_hit": bool(result.cache_hit),
            "attempts": int(result.attempts),
            "blocked_by": list(result.blocked_by),
            "degrade_reason": getattr(record, "degrade_reason", None),
            "output_keys": sorted(outputs.keys()),
            "output_hints": {
                key: cls._compact_value(value)
                for key, value in outputs.items()
            },
        }

    @staticmethod
    def _compact_value(value: Any) -> Any:
        if isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            text = value.strip()
            return text if len(text) <= 80 else f"{text[:77]}..."
        if isinstance(value, list):
            return {"type": "list", "count": len(value)}
        if isinstance(value, dict):
            return {"type": "dict", "keys": sorted(str(key) for key in value.keys())[:8], "count": len(value)}
        if value is None:
            return None
        return str(type(value).__name__)

    @staticmethod
    def _patch_requests_replan(patch: SubagentGraphPatch | None) -> bool:
        if patch is None:
            return False
        if patch.decision_type == "replan":
            return True
        return any(operation.op_type == "request_replan" for operation in patch.operations)


__all__ = ["SubagentPhaseLLMShadowPolicy", "SubagentPhaseShadowSuggestionPayload"]
