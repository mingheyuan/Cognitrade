from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from research_system.agents._subagent_dag import SubagentDagNodeResult
from research_system.runtime.core.execution_policy import derive_llm_error_reason
from research_system.runtime.providers.provider_registry import LLMProviderError
from research_system.schemas.subagents import SubagentGraphPatch, SubagentPlan, SubagentPlanNode


class SubagentPhaseShadowSuggestionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_plan_variant: str | None = None
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

    def suggest(
        self,
        *,
        dag_id: str,
        plan: SubagentPlan,
        candidate_plans: dict[str, SubagentPlan],
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
        if not self._enabled_for_suggestion(binding=binding):
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
        if not candidate_plans:
            return None
        candidate_nodes = self._candidate_nodes(candidate_plans=candidate_plans)
        plan_variants = {
            variant_id: [self._plan_node_summary(node) for node in current_plan.nodes]
            for variant_id, current_plan in candidate_plans.items()
        }
        prompt_payload = {
            "intent": intent,
            "phase": phase,
            "dag_id": dag_id,
            "plan_strategy": plan.strategy,
            "current_plan_nodes": [self._plan_node_summary(node) for node in plan.nodes],
            "plan_variants": plan_variants,
            "candidate_plan_nodes": [self._plan_node_summary(node) for node in candidate_nodes],
            "base_result_summary": [self._result_summary(node_id=node_id, result=result) for node_id, result in base_results.items()],
        }
        try:
            suggestion = self.llm_provider.parse(
                system_prompt=(
                    "You are the shadow phase-policy for a controlled subagent DAG orchestrator.\n"
                    "You do not execute or mutate the DAG.\n"
                    "Choose the best plan variant from the provided plan_variants and decide whether the phase should request a replan.\n"
                    "selected_plan_variant must be one of the provided plan_variants keys.\n"
                    "Also return which node ids should appear in the working plan after this control step.\n"
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
        selected_plan_variant = str(suggestion.selected_plan_variant or "").strip() or None
        if selected_plan_variant not in candidate_plans:
            selected_plan_variant = self._variant_from_selected_nodes(
                selected_node_ids=list(suggestion.selected_node_ids or []),
                candidate_plans=candidate_plans,
            )
        selected_node_ids = [
            node_id
            for node_id in list(suggestion.selected_node_ids or [])
            if node_id in candidate_node_ids
        ]
        payload = {
            "policy_source": "llm_shadow",
            "intent": intent,
            "phase": phase,
            "agent": agent,
            "dag_id": dag_id,
            "plan_id": plan.plan_id,
            "selected_plan_variant": selected_plan_variant,
            "selected_node_ids": selected_node_ids,
            "request_replan": bool(suggestion.request_replan),
            "reason": suggestion.reason,
            "confidence": suggestion.confidence,
            "notes": list(suggestion.notes or []),
            "plan_variants": {
                variant_id: [node.node_id for node in current_plan.nodes]
                for variant_id, current_plan in candidate_plans.items()
            },
            "candidate_node_ids": candidate_node_ids,
        }
        return payload

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
        payload = shadow_payload or self.suggest(
            dag_id=dag_id,
            plan=plan,
            candidate_plans=candidate_plans,
            base_results=base_results,
        )
        if payload is None:
            return None
        enriched_payload = self._enrich_with_actuals(
            payload=payload,
            actual_final_plan=actual_final_plan,
            actual_patch=actual_patch,
            actual_plan_variant=actual_plan_variant,
        )
        self._record(enriched_payload)
        return enriched_payload

    def _record(self, payload: dict[str, Any]) -> None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(payload.get("intent") or "").strip()
        if context is None or support is None or not request_id or not intent:
            return
        event_payload = {"event": "subagent_phase_shadow_suggested", **dict(payload)}
        context.subagent_phase_shadow_suggestions.append(dict(payload))
        context.dynamic_trace.append(dict(event_payload))
        if callable(self.emit_event):
            self.emit_event(
                event_type="subagent_phase_shadow_suggested",
                message=f"Shadow subagent-phase policy evaluated DAG {payload.get('dag_id')}",
                payload=dict(event_payload),
                phase=str(payload.get("phase") or "").strip() or None,
                agent=str(payload.get("agent") or "").strip() or None,
            )
        else:
            support.emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type="subagent_phase_shadow_suggested",
                phase=str(payload.get("phase") or "").strip() or None,
                agent=str(payload.get("agent") or "").strip() or None,
                message=f"Shadow subagent-phase policy evaluated DAG {payload.get('dag_id')}",
                payload=dict(event_payload),
            )

    def _enabled_for_suggestion(self, *, binding: dict[str, Any]) -> bool:
        if self.llm_provider is None:
            return False
        if not bool(getattr(self.llm_provider, "available", True)):
            return False
        if not (
            bool(getattr(self.settings, "subagent_phase_shadow_llm_enabled", False))
            or bool(getattr(self.settings, "subagent_phase_llm_policy_enabled", False))
            or bool(getattr(self.settings, "subagent_phase_hybrid_llm_enabled", False))
        ):
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
    def _candidate_nodes(*, candidate_plans: dict[str, SubagentPlan]) -> list[SubagentPlanNode]:
        indexed: dict[str, SubagentPlanNode] = {}
        for current_plan in candidate_plans.values():
            for node in list(current_plan.nodes):
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
    def _variant_from_selected_nodes(
        *,
        selected_node_ids: list[str],
        candidate_plans: dict[str, SubagentPlan],
    ) -> str | None:
        selected = set(str(node_id) for node_id in selected_node_ids if str(node_id).strip())
        if not selected:
            return None
        for variant_id, plan in candidate_plans.items():
            if selected == {node.node_id for node in plan.nodes}:
                return variant_id
        return None

    @staticmethod
    def _enrich_with_actuals(
        *,
        payload: dict[str, Any],
        actual_final_plan: SubagentPlan,
        actual_patch: SubagentGraphPatch | None,
        actual_plan_variant: str | None,
    ) -> dict[str, Any]:
        actual_final_node_ids = [node.node_id for node in actual_final_plan.nodes]
        actual_request_replan = SubagentPhaseLLMShadowPolicy._patch_requests_replan(actual_patch)
        return {
            "event": "subagent_phase_shadow_suggested",
            **dict(payload),
            "final_plan_id": actual_final_plan.plan_id,
            "actual_plan_variant": actual_plan_variant,
            "actual_final_node_ids": actual_final_node_ids,
            "actual_request_replan": actual_request_replan,
            "plan_match": (
                str(payload.get("selected_plan_variant") or "").strip() == str(actual_plan_variant or "").strip()
                if payload.get("selected_plan_variant")
                else set(list(payload.get("selected_node_ids") or [])) == set(actual_final_node_ids)
            ),
            "replan_match": bool(payload.get("request_replan")) == actual_request_replan,
        }

    @staticmethod
    def _patch_requests_replan(patch: SubagentGraphPatch | None) -> bool:
        if patch is None:
            return False
        if patch.decision_type == "replan":
            return True
        return any(operation.op_type == "request_replan" for operation in patch.operations)


__all__ = ["SubagentPhaseLLMShadowPolicy", "SubagentPhaseShadowSuggestionPayload"]
