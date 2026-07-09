from __future__ import annotations

from typing import Any

from research_system.agents._subagent_control_escalation import (
    SubagentControlEscalationDecision,
)
from research_system.agents._subagent_runtime_control_gateway import (
    SubagentRuntimeControlGateway,
)
from research_system.schemas.subagents import SubagentCapabilityGap


class SubagentCapabilityGapEscalationController:
    def __init__(
        self,
        *,
        escalation_policy=None,
        runtime_control_gateway=None,
        runtime_binding_getter=None,
    ) -> None:
        self.escalation_policy = escalation_policy
        self.runtime_control_gateway = runtime_control_gateway or SubagentRuntimeControlGateway(
            runtime_binding_getter=runtime_binding_getter,
        )
        self.runtime_binding_getter = runtime_binding_getter

    def resolve(
        self,
        *,
        gap: SubagentCapabilityGap,
        preferred_subagent_type: str | None,
        requires_llm: bool | None,
    ) -> SubagentControlEscalationDecision:
        return self.runtime_control_gateway.resolve_capability_gap_escalation(
            escalation_controller=self,
            gap=gap,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
        )

    def resolve_local(
        self,
        *,
        gap: SubagentCapabilityGap,
        preferred_subagent_type: str | None,
        requires_llm: bool | None,
    ) -> SubagentControlEscalationDecision:
        if self.escalation_policy is None:
            return SubagentControlEscalationDecision(
                strategy="discovery",
                reason="subagent_control_escalation=discovery",
                notes=("subagent_control_escalation_rule=default",),
            )
        decision = self.escalation_policy.resolve(
            intent=gap.intent,
            stage=gap.stage,
            capability=gap.capability,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
        )
        if decision.strategy != "action_replan":
            return decision
        if not self._action_replan_already_requested(gap=gap):
            return decision
        return SubagentControlEscalationDecision(
            strategy="discovery",
            policy_source=decision.policy_source,
            rule_id=decision.rule_id,
            reason="subagent_control_escalation=discovery",
            notes=tuple(
                [
                    *list(decision.notes or ()),
                    "action_replan_guarded_repeat_capability_gap",
                ]
            ),
            metadata=dict(decision.metadata or {}),
        )

    def _runtime_binding(self) -> dict[str, Any]:
        if callable(self.runtime_binding_getter):
            try:
                return dict(self.runtime_binding_getter() or {})
            except Exception:
                return {}
        return {}

    def _action_replan_already_requested(self, *, gap: SubagentCapabilityGap) -> bool:
        binding = self._runtime_binding()
        context = binding.get("context")
        patches = getattr(context, "action_plan_patches", None) if context is not None else None
        if not isinstance(patches, list):
            return False
        for patch_row in patches:
            if not isinstance(patch_row, dict):
                continue
            for operation in list(patch_row.get("operations") or []):
                if not isinstance(operation, dict):
                    continue
                if str(operation.get("op_type") or "") != "request_replan":
                    continue
                metadata = dict(operation.get("metadata") or {})
                if str(metadata.get("control_source") or "") != "subagent_capability_gap":
                    continue
                if str(metadata.get("intent") or "") != str(gap.intent or ""):
                    continue
                if str(metadata.get("stage") or "") != str(gap.stage or ""):
                    continue
                if str(metadata.get("capability") or "") != str(gap.capability or ""):
                    continue
                return True
        return False


__all__ = ["SubagentCapabilityGapEscalationController"]
