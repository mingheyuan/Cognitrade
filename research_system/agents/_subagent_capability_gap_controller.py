from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_system.agents._subagent_capability_gap_escalation_controller import (
    SubagentCapabilityGapEscalationController,
)
from research_system.agents._subagent_control_escalation import (
    SubagentControlEscalationDecision,
    SubagentControlEscalationError,
)
from research_system.agents._subagent_discovery_controller import SubagentDiscoveryController
from research_system.schemas.planning import ActionPlanPatch, ActionPlanPatchOperation
from research_system.schemas.subagents import (
    SubagentCapabilityGap,
    SubagentCatalogEntry,
    SubagentToolDiscoveryRequest,
)


@dataclass(frozen=True)
class SubagentCapabilityGapResolution:
    gap: SubagentCapabilityGap
    tool_discovery_request: SubagentToolDiscoveryRequest
    ticket: Any | None = None
    selected_entry: SubagentCatalogEntry | None = None
    escalation_decision: SubagentControlEscalationDecision | None = None


class SubagentCapabilityGapController:
    def __init__(
        self,
        *,
        capability_gap_builder,
        discovery_controller: SubagentDiscoveryController,
        candidates_resolver,
        entry_selector,
        escalation_policy=None,
        escalation_controller=None,
        discovery_store_resolver=None,
        runtime_binding_getter=None,
        record_capability_gap=None,
        record_tool_discovery_request=None,
        record_tool_discovery_auto_activation=None,
        record_escalation_decision=None,
        error_factory=RuntimeError,
    ) -> None:
        self.capability_gap_builder = capability_gap_builder
        self.discovery_controller = discovery_controller
        self.candidates_resolver = candidates_resolver
        self.entry_selector = entry_selector
        self.discovery_store_resolver = discovery_store_resolver
        self.escalation_controller = escalation_controller or SubagentCapabilityGapEscalationController(
            escalation_policy=escalation_policy,
            runtime_binding_getter=runtime_binding_getter,
        )
        self.record_capability_gap = record_capability_gap
        self.record_tool_discovery_request = record_tool_discovery_request
        self.record_tool_discovery_auto_activation = record_tool_discovery_auto_activation
        self.record_escalation_decision = record_escalation_decision
        self.error_factory = error_factory

    def resolve_capability_gap(
        self,
        *,
        stage: str,
        intent: str,
        capability: str,
        preferred_subagent_type: str | None = None,
        requires_llm: bool | None = None,
    ) -> SubagentCapabilityGapResolution:
        gap = self.capability_gap_builder(
            stage=stage,
            intent=intent,
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
        )
        escalation_decision = self._resolve_escalation_decision(
            gap=gap,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
        )
        if escalation_decision.strategy == "action_replan":
            self._record_gap_only(gap=gap)
            self._record_escalation_decision(
                gap=gap,
                escalation_decision=escalation_decision,
            )
            raise SubagentControlEscalationError(
                gap=gap,
                escalation_decision=escalation_decision,
                plan_patch=self._build_action_replan_patch(
                    gap=gap,
                    escalation_decision=escalation_decision,
                ),
            )
        discovery_resolution = self.discovery_controller.resolve_runtime_gap(
            gap=gap,
            store=self._resolve_discovery_store(),
        )
        request = discovery_resolution.tool_discovery_request
        ticket = discovery_resolution.ticket
        self._record_gap_resolution(
            gap=gap,
            request=request,
            ticket=ticket,
        )
        candidates = self.candidates_resolver(
            stage=stage,
            intent=intent,
            capability=capability,
            requires_llm=requires_llm,
        )
        selected_entry = None
        if candidates:
            selected_entry = self.entry_selector(
                candidates,
                preferred_subagent_type=preferred_subagent_type,
            )
        return SubagentCapabilityGapResolution(
            gap=gap,
            tool_discovery_request=request,
            ticket=ticket,
            selected_entry=selected_entry,
            escalation_decision=escalation_decision,
        )

    def _resolve_escalation_decision(
        self,
        *,
        gap: SubagentCapabilityGap,
        preferred_subagent_type: str | None,
        requires_llm: bool | None,
    ) -> SubagentControlEscalationDecision:
        return self.escalation_controller.resolve(
            gap=gap,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
        )

    def _resolve_discovery_store(self):
        if not callable(self.discovery_store_resolver):
            return None
        try:
            return self.discovery_store_resolver()
        except Exception:
            return None

    def _record_gap_resolution(
        self,
        *,
        gap: SubagentCapabilityGap,
        request: SubagentToolDiscoveryRequest,
        ticket,
    ) -> None:
        if callable(self.record_capability_gap):
            self.record_capability_gap(gap=gap)
        if callable(self.record_tool_discovery_request):
            self.record_tool_discovery_request(request=request)
        if callable(self.record_tool_discovery_auto_activation):
            self.record_tool_discovery_auto_activation(ticket=ticket)

    def _record_gap_only(self, *, gap: SubagentCapabilityGap) -> None:
        if callable(self.record_capability_gap):
            self.record_capability_gap(gap=gap)

    def _record_escalation_decision(
        self,
        *,
        gap: SubagentCapabilityGap,
        escalation_decision: SubagentControlEscalationDecision,
    ) -> None:
        if callable(self.record_escalation_decision):
            self.record_escalation_decision(
                gap=gap,
                escalation_decision=escalation_decision,
            )

    def _build_action_replan_patch(
        self,
        *,
        gap: SubagentCapabilityGap,
        escalation_decision: SubagentControlEscalationDecision,
    ) -> ActionPlanPatch:
        metadata = {
            "control_source": "subagent_capability_gap",
            "intent": str(gap.intent or ""),
            "stage": str(gap.stage or ""),
            "capability": str(gap.capability or ""),
        }
        if gap.preferred_subagent_type:
            metadata["preferred_subagent_type"] = str(gap.preferred_subagent_type)
        if gap.requires_llm is not None:
            metadata["requires_llm"] = "true" if bool(gap.requires_llm) else "false"
        if escalation_decision.rule_id:
            metadata["escalation_rule_id"] = str(escalation_decision.rule_id)
        metadata.update(
            {
                str(key): str(value)
                for key, value in dict(escalation_decision.metadata or {}).items()
                if str(key).strip() and str(value).strip()
            }
        )
        reason = (
            "subagent capability gap requires action replan: "
            f"intent={gap.intent} stage={gap.stage} capability={gap.capability}"
        )
        return ActionPlanPatch(
            decision_type="replan",
            operations=[
                ActionPlanPatchOperation(
                    op_type="request_replan",
                    reason=reason,
                    metadata=metadata,
                )
            ],
            notes=[
                "request_action_plan_replan",
                "subagent_capability_gap_action_replan",
                *list(escalation_decision.notes or ()),
            ],
            policy_source=escalation_decision.policy_source,
            confidence=1.0,
            termination_reason=reason,
        )

    def build_unresolved_gap_error(
        self,
        capability_gap_resolution: SubagentCapabilityGapResolution | None,
    ) -> Exception:
        if capability_gap_resolution is None:
            return self.error_factory("unable to resolve catalog entry")
        try:
            return self.error_factory(
                gap=capability_gap_resolution.gap,
                tool_discovery_request=capability_gap_resolution.tool_discovery_request,
            )
        except TypeError:
            return self.error_factory(capability_gap_resolution.gap.reason)


__all__ = [
    "SubagentCapabilityGapController",
    "SubagentCapabilityGapResolution",
]
