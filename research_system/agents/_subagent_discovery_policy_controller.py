from __future__ import annotations

from dataclasses import dataclass

from research_system.agents._subagent_discovery_decision_source import (
    SubagentDiscoveryDecisionSource,
)
from research_system.agents._subagent_discovery_stage_decisions import (
    SubagentDiscoveryActivationStageDecision,
    SubagentDiscoveryActivationTarget,
    SubagentDiscoveryCandidateStageResult,
    SubagentDiscoveryReviewStageDecision,
)
from research_system.schemas.subagents import SubagentDiscoveryTicket


@dataclass(frozen=True)
class SubagentDiscoveryActivationResolution:
    activation_decision: SubagentDiscoveryActivationStageDecision
    activation_target: SubagentDiscoveryActivationTarget | None = None
    resolution_error: str | None = None


@dataclass(frozen=True)
class SubagentDiscoveryReviewResolution:
    review_decision: SubagentDiscoveryReviewStageDecision
    candidate_result: SubagentDiscoveryCandidateStageResult | None = None


@dataclass(frozen=True)
class SubagentDiscoveryPreviewResolution:
    candidate_result: SubagentDiscoveryCandidateStageResult
    review_decision: SubagentDiscoveryReviewStageDecision
    activation_resolution: SubagentDiscoveryActivationResolution | None = None


class SubagentDiscoveryPolicyController:
    def __init__(
        self,
        *,
        decision_source: SubagentDiscoveryDecisionSource,
        runtime_packaging,
        error_factory,
    ) -> None:
        self.decision_source = decision_source
        self.runtime_packaging = runtime_packaging
        self.error_factory = error_factory

    def resolve_runtime_preview(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
    ) -> SubagentDiscoveryPreviewResolution:
        preview_ticket = ticket.model_copy(deep=True)
        candidate_result = self.decision_source.select_candidate(ticket=preview_ticket)
        self.decision_source.apply_candidate_selection(
            ticket=preview_ticket,
            candidate_result=candidate_result,
        )
        review_decision = self.decision_source.evaluate_review(ticket=preview_ticket)
        self.decision_source.apply_review(
            ticket=preview_ticket,
            review_decision=review_decision,
        )
        activation_resolution = None
        if str(review_decision.decision or "").strip().lower() == "approved":
            activation_resolution = self.resolve_runtime_activation(ticket=preview_ticket)
        return SubagentDiscoveryPreviewResolution(
            candidate_result=candidate_result,
            review_decision=review_decision,
            activation_resolution=activation_resolution,
        )

    def resolve_runtime_activation(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
    ) -> SubagentDiscoveryActivationResolution:
        activation_decision = self.decision_source.evaluate_activation(ticket=ticket)
        if not activation_decision.activate:
            return SubagentDiscoveryActivationResolution(
                activation_decision=activation_decision,
                activation_target=None,
            )
        try:
            activation_target = self.decision_source.resolve_activation_target(
                ticket=ticket,
                candidate_subagent_type=activation_decision.candidate_subagent_type,
                delegate_subagent_type=None,
                registration_template_id=activation_decision.registration_template_id,
                registration_type=None,
                skill_name=None,
                snapshot_type=None,
                source_id=None,
                tool_name=None,
                endpoint_path=None,
                http_method=None,
                openapi_spec_uri=None,
                openapi_operation_id=None,
                timeout_seconds=None,
                runtime_packaging=self.runtime_packaging,
                error_factory=self.error_factory,
            )
        except Exception as exc:
            return SubagentDiscoveryActivationResolution(
                activation_decision=activation_decision,
                activation_target=None,
                resolution_error=str(exc).strip() or exc.__class__.__name__,
            )
        bound_activation_decision = self.decision_source.materialize_activation_decision(
            activation_decision=activation_decision,
            activation_target=activation_target,
            activated_by="system_auto_activate",
            reason=activation_decision.reason,
        )
        return SubagentDiscoveryActivationResolution(
            activation_decision=bound_activation_decision,
            activation_target=activation_target,
        )

    def resolve_manual_review(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        decision: str,
        reviewer: str,
        reason: str | None = None,
        source: str = "manual_cli_review",
        candidate_catalog_entries: list[dict] | None = None,
    ) -> SubagentDiscoveryReviewResolution:
        review_ticket = ticket.model_copy(deep=True)
        candidate_result = None
        if str(decision or "").strip().lower() == "approve":
            candidate_result = self.decision_source.select_candidate(
                ticket=review_ticket,
                candidate_entries=candidate_catalog_entries,
            )
            self.decision_source.apply_candidate_selection(
                ticket=review_ticket,
                candidate_result=candidate_result,
            )
        review_decision = self.decision_source.build_manual_review(
            ticket=review_ticket,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            source=source,
        )
        return SubagentDiscoveryReviewResolution(
            review_decision=review_decision,
            candidate_result=candidate_result,
        )

    def resolve_manual_activation(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        delegate_subagent_type: str | None = None,
        candidate_subagent_type: str | None = None,
        registration_template_id: str | None = None,
        activated_by: str = "manual_cli_activation",
        reason: str | None = None,
        registration_type: str | None = None,
        skill_name: str | None = None,
        snapshot_type: str | None = None,
        source_id: str | None = None,
        tool_name: str | None = None,
        endpoint_path: str | None = None,
        http_method: str | None = None,
        openapi_spec_uri: str | None = None,
        openapi_operation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> SubagentDiscoveryActivationResolution:
        activation_target = self.decision_source.resolve_activation_target(
            ticket=ticket,
            candidate_subagent_type=candidate_subagent_type,
            delegate_subagent_type=delegate_subagent_type,
            registration_template_id=registration_template_id,
            registration_type=registration_type,
            skill_name=skill_name,
            snapshot_type=snapshot_type,
            source_id=source_id,
            tool_name=tool_name,
            endpoint_path=endpoint_path,
            http_method=http_method,
            openapi_spec_uri=openapi_spec_uri,
            openapi_operation_id=openapi_operation_id,
            timeout_seconds=timeout_seconds,
            runtime_packaging=self.runtime_packaging,
            error_factory=self.error_factory,
        )
        activation_decision = self.decision_source.build_manual_activation(
            ticket=ticket,
            activation_target=activation_target,
            activated_by=activated_by,
            reason=reason,
        )
        return SubagentDiscoveryActivationResolution(
            activation_decision=activation_decision,
            activation_target=activation_target,
        )


__all__ = [
    "SubagentDiscoveryActivationResolution",
    "SubagentDiscoveryReviewResolution",
    "SubagentDiscoveryPolicyController",
    "SubagentDiscoveryPreviewResolution",
]
