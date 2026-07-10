from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from research_system.agents._subagent_discovery_policy_controller import (
    SubagentDiscoveryActivationResolution,
    SubagentDiscoveryPolicyController,
)
from research_system.runtime.orchestration.subagent_discovery_audit_recorder import (
    SubagentDiscoveryAuditRecorder,
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
        control_stage_recorder=None,
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
        self.control_stage_recorder = control_stage_recorder or SubagentDiscoveryAuditRecorder(
            runtime_binding_getter=runtime_binding_getter,
            emit_event=emit_event,
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
        self._record_stage_decision(protocol_decision)
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
        preview_application = self.discovery_policy_controller.apply_runtime_preview(
            ticket=ticket,
            store=store,
            raise_on_save_error=False,
        )
        preview_resolution = preview_application.preview_resolution
        candidate_result = preview_resolution.candidate_result
        self._record_stage_decision(candidate_result.decision)
        approval_decision = preview_application.review_application.review_resolution.review_decision
        self._record_stage_decision(approval_decision)
        if not preview_application.review_application.persisted:
            return ticket
        if preview_application.activation_application is not None:
            return self.maybe_auto_activate_ticket(
                ticket=ticket,
                store=store,
                activation_resolution=preview_application.activation_application.activation_resolution,
                activation_application=preview_application.activation_application,
            )
        return ticket

    def maybe_auto_activate_ticket(
        self,
        *,
        ticket,
        store,
        activation_resolution: SubagentDiscoveryActivationResolution | None = None,
        activation_application=None,
    ):
        activation_application = activation_application or self.discovery_policy_controller.apply_activation_resolution(
            ticket=ticket,
            activation_resolution=activation_resolution or self.discovery_policy_controller.resolve_runtime_activation(
                ticket=ticket,
            ),
            store=store,
            previous_status="approved",
            raise_on_save_error=False,
        )
        activation_decision = activation_application.activation_resolution.activation_decision
        if activation_application.activation_outcome == "skipped":
            self._record_activation_decision(
                activation_decision,
                activation_outcome="skipped",
            )
            return ticket
        if activation_application.activation_outcome == "failed":
            self._record_activation_decision(
                activation_decision,
                activation_outcome="failed",
            )
            return ticket
        self._record_activation_decision(
            activation_decision,
            activation_outcome="activated",
        )
        return ticket

    def _record_stage_decision(
        self,
        decision,
    ) -> None:
        payload = decision.event_payload()
        phase = str(payload.get("stage") or "").strip() or None
        agent = self.service_name
        if self.control_stage_recorder is None:
            return
        self.control_stage_recorder.record(
            event_type=decision.event_type,
            message=decision.message(),
            payload=payload,
            phase=phase,
            agent=agent,
            extra_context_attrs=["subagent_discovery_stage_decisions"],
        )

    def _record_activation_decision(
        self,
        decision,
        *,
        activation_outcome: Literal["skipped", "failed", "activated"],
    ) -> None:
        payload = decision.event_payload(activation_outcome=activation_outcome)
        phase = str(payload.get("stage") or "").strip() or None
        agent = self.service_name
        if self.control_stage_recorder is None:
            return
        self.control_stage_recorder.record(
            event_type=decision.event_type,
            message=decision.message(activation_outcome=activation_outcome),
            payload=payload,
            phase=phase,
            agent=agent,
            extra_context_attrs=["subagent_discovery_stage_decisions"],
        )


__all__ = ["SubagentDiscoveryController", "SubagentDiscoveryResolution"]
