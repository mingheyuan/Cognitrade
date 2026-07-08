from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_system.agents._subagent_discovery_policy_controller import (
    SubagentDiscoveryActivationResolution,
    SubagentDiscoveryPolicyController,
)
from research_system.schemas.subagents import (
    SubagentCapabilityGap,
    SubagentToolDiscoveryRequest,
)


@dataclass(frozen=True)
class SubagentDiscoveryResolution:
    tool_discovery_request: SubagentToolDiscoveryRequest
    ticket: Any | None = None


class SubagentDiscoveryController:
    def __init__(
        self,
        *,
        discovery_candidate_support,
        discovery_decision_source,
        activation_host,
        discovery_policy_controller=None,
        runtime_binding_getter=None,
        emit_event=None,
        service_name: str | None = None,
    ) -> None:
        self.discovery_candidate_support = discovery_candidate_support
        self.discovery_decision_source = discovery_decision_source
        self.activation_host = activation_host
        self.discovery_policy_controller = (
            discovery_policy_controller
            if discovery_policy_controller is not None
            else SubagentDiscoveryPolicyController(
                decision_source=discovery_decision_source,
                runtime_packaging=getattr(discovery_candidate_support, "runtime_packaging", None),
                error_factory=getattr(activation_host, "orchestrator_error_cls", RuntimeError),
            )
        )
        self.runtime_binding_getter = runtime_binding_getter
        self.emit_event = emit_event
        self.service_name = service_name or "subagent_discovery"

    def resolve_runtime_gap(
        self,
        *,
        gap: SubagentCapabilityGap,
        store=None,
    ) -> SubagentDiscoveryResolution:
        request = self.build_request(gap=gap)
        ticket = self.persist_runtime_request(gap=gap, request=request, store=store)
        return SubagentDiscoveryResolution(
            tool_discovery_request=request,
            ticket=ticket,
        )

    def build_request(
        self,
        *,
        gap: SubagentCapabilityGap,
    ) -> SubagentToolDiscoveryRequest:
        protocol_decision = self.discovery_decision_source.resolve_protocols(gap=gap)
        request = protocol_decision.request
        self._record_stage_decision(
            event_name="subagent_tool_discovery_protocol_decided",
            payload=protocol_decision.event_payload(),
        )
        return request

    def persist_runtime_request(
        self,
        *,
        gap: SubagentCapabilityGap,
        request: SubagentToolDiscoveryRequest,
        store,
    ):
        create_request = getattr(store, "create_request", None)
        if not callable(create_request):
            return None
        try:
            ticket = create_request(
                gap=gap,
                request=request,
            )
        except Exception:
            return None
        return self.preview_ticket(ticket=ticket, store=store)

    def preview_ticket(self, *, ticket, store):
        preview_resolution = self.discovery_policy_controller.resolve_runtime_preview(
            ticket=ticket,
        )
        candidate_result = preview_resolution.candidate_result
        self.discovery_decision_source.apply_candidate_selection(
            ticket=ticket,
            candidate_result=candidate_result,
        )
        self._record_stage_decision(
            event_name="subagent_tool_discovery_candidate_decided",
            payload=candidate_result.decision.event_payload(),
        )
        approval_decision = preview_resolution.review_decision
        previous_status = self.discovery_decision_source.apply_review(
            ticket=ticket,
            review_decision=approval_decision,
        )
        self._record_stage_decision(
            event_name="subagent_tool_discovery_review_decided",
            payload=approval_decision.event_payload(),
        )
        save = getattr(store, "save", None)
        if callable(save):
            try:
                save(ticket, previous_status=previous_status)
            except Exception:
                return ticket
        if preview_resolution.activation_resolution is not None:
            return self.maybe_auto_activate_ticket(
                ticket=ticket,
                store=store,
                activation_resolution=preview_resolution.activation_resolution,
            )
        return ticket

    def maybe_auto_activate_ticket(self, *, ticket, store, activation_resolution: SubagentDiscoveryActivationResolution | None = None):
        activation_resolution = activation_resolution or self.discovery_policy_controller.resolve_runtime_activation(
            ticket=ticket,
        )
        activation_decision = activation_resolution.activation_decision
        if not activation_decision.activate:
            self._record_stage_decision(
                event_name="subagent_tool_discovery_activation_decided",
                payload=activation_decision.event_payload(activation_outcome="skipped"),
            )
            return ticket
        activation_target = activation_resolution.activation_target
        if activation_target is None:
            self._record_stage_decision(
                event_name="subagent_tool_discovery_activation_decided",
                payload=activation_decision.event_payload(activation_outcome="failed"),
            )
            return ticket
        try:
            _activation_entry, created = self.discovery_decision_source.apply_activation(
                ticket=ticket,
                activation_decision=activation_decision,
                candidate_entry=activation_target.candidate_entry,
            )
        except Exception:
            self._record_stage_decision(
                event_name="subagent_tool_discovery_activation_decided",
                payload=activation_decision.event_payload(activation_outcome="failed"),
            )
            return ticket
        save = getattr(store, "save", None)
        if created and callable(save):
            try:
                save(ticket, previous_status="approved")
            except Exception:
                self._record_stage_decision(
                    event_name="subagent_tool_discovery_activation_decided",
                    payload=activation_decision.event_payload(activation_outcome="failed"),
                )
                return ticket
        if not created:
            self._record_stage_decision(
                event_name="subagent_tool_discovery_activation_decided",
                payload=activation_decision.event_payload(activation_outcome="activated"),
            )
            return ticket
        self._record_stage_decision(
            event_name="subagent_tool_discovery_activation_decided",
            payload=activation_decision.event_payload(activation_outcome="activated"),
        )
        return ticket

    def _record_stage_decision(
        self,
        *,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(payload.get("intent") or "").strip()
        phase = str(payload.get("stage") or "").strip() or None
        agent = str(binding.get("agent") or self.service_name).strip() or self.service_name
        if context is None or support is None or not request_id or not intent:
            return
        event_payload = {"event": event_name, **dict(payload), "agent": agent}
        decision_rows = getattr(context, "subagent_discovery_stage_decisions", None)
        if not isinstance(decision_rows, list):
            try:
                setattr(context, "subagent_discovery_stage_decisions", [])
            except Exception:
                decision_rows = None
            else:
                decision_rows = getattr(context, "subagent_discovery_stage_decisions", None)
        if isinstance(decision_rows, list):
            decision_rows.append(dict(event_payload))
        dynamic_trace = getattr(context, "dynamic_trace", None)
        if isinstance(dynamic_trace, list):
            dynamic_trace.append(dict(event_payload))
        message = (
            f"{event_name} resolved discovery {payload.get('decision_stage')} "
            f"for capability {payload.get('capability')}"
        )
        if callable(self.emit_event):
            self.emit_event(
                event_type=event_name,
                message=message,
                payload=dict(event_payload),
                phase=phase,
                agent=agent,
            )
            return
        support.emit_event(
            context,
            request_id=request_id,
            intent=intent,
            event_type=event_name,
            phase=phase,
            agent=agent,
            message=message,
            payload=dict(event_payload),
        )


__all__ = ["SubagentDiscoveryController", "SubagentDiscoveryResolution"]
