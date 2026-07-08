from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from research_system.agents._subagent_discovery_decision_source import (
    SubagentDiscoveryDecisionSource,
)
from research_system.agents._subagent_discovery_stage_policy_source import (
    SubagentDiscoveryStagePolicySource,
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


@dataclass(frozen=True)
class SubagentDiscoveryReviewApplication:
    review_resolution: SubagentDiscoveryReviewResolution
    previous_status: str
    persisted: bool = True
    persistence_error: str | None = None


@dataclass(frozen=True)
class SubagentDiscoveryActivationApplication:
    activation_resolution: SubagentDiscoveryActivationResolution
    activation_outcome: Literal["skipped", "failed", "activated"]
    created: bool = False
    persisted: bool = True
    persistence_error: str | None = None


@dataclass(frozen=True)
class SubagentDiscoveryPreviewApplication:
    preview_resolution: SubagentDiscoveryPreviewResolution
    review_application: SubagentDiscoveryReviewApplication
    activation_application: SubagentDiscoveryActivationApplication | None = None


class SubagentDiscoveryPolicyController:
    def __init__(
        self,
        *,
        decision_source: SubagentDiscoveryDecisionSource,
        stage_policy_source: SubagentDiscoveryStagePolicySource | None = None,
        runtime_packaging,
        error_factory,
    ) -> None:
        self.decision_source = decision_source
        self.stage_policy_source = (
            stage_policy_source
            if stage_policy_source is not None
            else decision_source.stage_policy_source
        )
        self.runtime_packaging = runtime_packaging
        self.error_factory = error_factory

    def resolve_runtime_preview(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
    ) -> SubagentDiscoveryPreviewResolution:
        preview_ticket = ticket.model_copy(deep=True)
        candidate_result = self.stage_policy_source.select_candidate(ticket=preview_ticket)
        self.decision_source.apply_candidate_selection(
            ticket=preview_ticket,
            candidate_result=candidate_result,
        )
        review_decision = self.stage_policy_source.evaluate_review(ticket=preview_ticket)
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
        activation_decision = self.stage_policy_source.evaluate_activation(ticket=ticket)
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

    def apply_runtime_preview(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        store=None,
        raise_on_save_error: bool = True,
    ) -> SubagentDiscoveryPreviewApplication:
        preview_resolution = self.resolve_runtime_preview(ticket=ticket)
        review_application = self.apply_review_resolution(
            ticket=ticket,
            review_resolution=SubagentDiscoveryReviewResolution(
                review_decision=preview_resolution.review_decision,
                candidate_result=preview_resolution.candidate_result,
            ),
            store=store,
            raise_on_save_error=raise_on_save_error,
        )
        activation_application = None
        if (
            preview_resolution.activation_resolution is not None
            and review_application.persisted
        ):
            activation_application = self.apply_activation_resolution(
                ticket=ticket,
                activation_resolution=preview_resolution.activation_resolution,
                store=store,
                previous_status="approved",
                raise_on_save_error=raise_on_save_error,
            )
        return SubagentDiscoveryPreviewApplication(
            preview_resolution=preview_resolution,
            review_application=review_application,
            activation_application=activation_application,
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
            candidate_result = self.stage_policy_source.select_candidate(
                ticket=review_ticket,
                candidate_entries=candidate_catalog_entries,
            )
            self.decision_source.apply_candidate_selection(
                ticket=review_ticket,
                candidate_result=candidate_result,
            )
        review_decision = self.stage_policy_source.build_manual_review(
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

    def apply_manual_review(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        decision: str,
        reviewer: str,
        reason: str | None = None,
        source: str = "manual_cli_review",
        candidate_catalog_entries: list[dict] | None = None,
        store=None,
        raise_on_save_error: bool = True,
    ) -> SubagentDiscoveryReviewApplication:
        review_resolution = self.resolve_manual_review(
            ticket=ticket,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            source=source,
            candidate_catalog_entries=candidate_catalog_entries,
        )
        return self.apply_review_resolution(
            ticket=ticket,
            review_resolution=review_resolution,
            store=store,
            raise_on_save_error=raise_on_save_error,
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
        activation_decision = self.stage_policy_source.build_manual_activation(
            ticket=ticket,
            activation_target=activation_target,
            activated_by=activated_by,
            reason=reason,
        )
        return SubagentDiscoveryActivationResolution(
            activation_decision=activation_decision,
            activation_target=activation_target,
        )

    def apply_manual_activation(
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
        store=None,
        raise_on_save_error: bool = True,
    ) -> SubagentDiscoveryActivationApplication:
        activation_resolution = self.resolve_manual_activation(
            ticket=ticket,
            delegate_subagent_type=delegate_subagent_type,
            candidate_subagent_type=candidate_subagent_type,
            registration_template_id=registration_template_id,
            activated_by=activated_by,
            reason=reason,
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
        )
        return self.apply_activation_resolution(
            ticket=ticket,
            activation_resolution=activation_resolution,
            store=store,
            previous_status="approved",
            raise_on_save_error=raise_on_save_error,
        )

    def apply_review_resolution(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        review_resolution: SubagentDiscoveryReviewResolution,
        store=None,
        raise_on_save_error: bool = True,
    ) -> SubagentDiscoveryReviewApplication:
        if review_resolution.candidate_result is not None:
            self.decision_source.apply_candidate_selection(
                ticket=ticket,
                candidate_result=review_resolution.candidate_result,
            )
        previous_status = self.decision_source.apply_review(
            ticket=ticket,
            review_decision=review_resolution.review_decision,
        )
        persisted, persistence_error = self._save_ticket(
            ticket=ticket,
            store=store,
            previous_status=previous_status,
            raise_on_save_error=raise_on_save_error,
        )
        return SubagentDiscoveryReviewApplication(
            review_resolution=review_resolution,
            previous_status=previous_status,
            persisted=persisted,
            persistence_error=persistence_error,
        )

    def apply_activation_resolution(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        activation_resolution: SubagentDiscoveryActivationResolution,
        store=None,
        previous_status: str = "approved",
        raise_on_save_error: bool = True,
    ) -> SubagentDiscoveryActivationApplication:
        activation_decision = activation_resolution.activation_decision
        if not activation_decision.activate:
            return SubagentDiscoveryActivationApplication(
                activation_resolution=activation_resolution,
                activation_outcome="skipped",
            )
        activation_target = activation_resolution.activation_target
        if activation_target is None:
            return SubagentDiscoveryActivationApplication(
                activation_resolution=activation_resolution,
                activation_outcome="failed",
            )
        try:
            _activation_entry, created = self.decision_source.apply_activation(
                ticket=ticket,
                activation_decision=activation_decision,
                candidate_entry=activation_target.candidate_entry,
            )
        except Exception as exc:
            return SubagentDiscoveryActivationApplication(
                activation_resolution=activation_resolution,
                activation_outcome="failed",
                persisted=False,
                persistence_error=str(exc).strip() or exc.__class__.__name__,
            )
        if not created:
            return SubagentDiscoveryActivationApplication(
                activation_resolution=activation_resolution,
                activation_outcome="activated",
                created=False,
            )
        persisted, persistence_error = self._save_ticket(
            ticket=ticket,
            store=store,
            previous_status=previous_status,
            raise_on_save_error=raise_on_save_error,
        )
        return SubagentDiscoveryActivationApplication(
            activation_resolution=activation_resolution,
            activation_outcome="activated" if persisted else "failed",
            created=created,
            persisted=persisted,
            persistence_error=persistence_error,
        )

    @staticmethod
    def _save_ticket(
        *,
        ticket: SubagentDiscoveryTicket,
        store,
        previous_status: str,
        raise_on_save_error: bool,
    ) -> tuple[bool, str | None]:
        save = getattr(store, "save", None)
        if not callable(save):
            return True, None
        try:
            save(ticket, previous_status=previous_status)
        except Exception as exc:
            if raise_on_save_error:
                raise
            return False, str(exc).strip() or exc.__class__.__name__
        return True, None


__all__ = [
    "SubagentDiscoveryActivationApplication",
    "SubagentDiscoveryActivationResolution",
    "SubagentDiscoveryPreviewApplication",
    "SubagentDiscoveryReviewApplication",
    "SubagentDiscoveryReviewResolution",
    "SubagentDiscoveryPolicyController",
    "SubagentDiscoveryPreviewResolution",
]
