from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from research_system.agents._subagent_discovery_stage_decisions import (
    SubagentDiscoveryActivationStageDecision,
    SubagentDiscoveryActivationTarget,
    SubagentDiscoveryCandidateStageResult,
    SubagentDiscoveryProtocolStageDecision,
    SubagentDiscoveryReviewStageDecision,
)
from research_system.agents._subagent_discovery_stage_policy_source import (
    SubagentDiscoveryStagePolicySource,
)
from research_system.schemas.subagents import (
    SubagentActivationEntry,
    SubagentCatalogEntry,
    SubagentDiscoveryReviewEvent,
    SubagentDiscoveryTicket,
)


class SubagentDiscoveryDecisionSource:
    def __init__(
        self,
        *,
        stage_policy_source: SubagentDiscoveryStagePolicySource | None = None,
        discovery_policy=None,
        discovery_llm_policy_source=None,
        discovery_candidate_policy=None,
        discovery_approval_policy=None,
        discovery_activation_policy=None,
        discovery_candidate_support=None,
    ) -> None:
        self.discovery_candidate_support = discovery_candidate_support
        if stage_policy_source is None:
            if (
                discovery_policy is None
                or discovery_llm_policy_source is None
                or discovery_candidate_policy is None
                or discovery_approval_policy is None
                or discovery_activation_policy is None
            ):
                raise ValueError(
                    "stage_policy_source or full discovery stage policy dependencies are required"
                )
        self.stage_policy_source = (
            stage_policy_source
            if stage_policy_source is not None
            else SubagentDiscoveryStagePolicySource(
                discovery_policy=discovery_policy,
                discovery_llm_policy_source=discovery_llm_policy_source,
                discovery_candidate_policy=discovery_candidate_policy,
                discovery_approval_policy=discovery_approval_policy,
                discovery_activation_policy=discovery_activation_policy,
                candidate_entries_resolver=self.resolve_candidate_entries,
            )
        )
        self.discovery_policy = self.stage_policy_source.discovery_policy
        self.discovery_llm_policy_source = self.stage_policy_source.discovery_llm_policy_source
        self.discovery_candidate_policy = self.stage_policy_source.discovery_candidate_policy
        self.discovery_approval_policy = self.stage_policy_source.discovery_approval_policy
        self.discovery_activation_policy = self.stage_policy_source.discovery_activation_policy

    def resolve_protocols(
        self,
        *,
        gap,
    ) -> SubagentDiscoveryProtocolStageDecision:
        return self.stage_policy_source.resolve_protocols(gap=gap)

    def select_candidate(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        candidate_entries: list[SubagentCatalogEntry] | None = None,
    ) -> SubagentDiscoveryCandidateStageResult:
        return self.stage_policy_source.select_candidate(
            ticket=ticket,
            candidate_entries=candidate_entries,
        )

    def resolve_candidate_entries(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        candidate_catalog_entries: list[Any] | None = None,
    ) -> list[SubagentCatalogEntry]:
        if candidate_catalog_entries:
            return [
                self._normalize_candidate_entry(SubagentCatalogEntry.model_validate(item))
                for item in candidate_catalog_entries
                if isinstance(item, dict) or isinstance(item, SubagentCatalogEntry)
            ]
        if ticket.candidate_catalog_entries:
            return [
                self._normalize_candidate_entry(entry)
                for entry in list(ticket.candidate_catalog_entries or [])
                if isinstance(entry, SubagentCatalogEntry)
            ]
        if self.discovery_candidate_support is not None:
            try:
                generated_entries = self.discovery_candidate_support.candidate_entries(ticket)
            except Exception:
                generated_entries = []
            if generated_entries:
                return [
                    self._normalize_candidate_entry(entry)
                    for entry in list(generated_entries or [])
                    if isinstance(entry, SubagentCatalogEntry)
                ]
        return [self._normalize_candidate_entry(self._placeholder_candidate_entry(ticket))]

    def evaluate_review(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
    ) -> SubagentDiscoveryReviewStageDecision:
        return self.stage_policy_source.evaluate_review(ticket=ticket)

    def build_manual_review(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        decision: str,
        reviewer: str,
        reason: str | None = None,
        source: str = "manual_cli_review",
        notes: list[str] | None = None,
    ) -> SubagentDiscoveryReviewStageDecision:
        return self.stage_policy_source.build_manual_review(
            ticket=ticket,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            source=source,
            notes=notes,
        )

    def evaluate_activation(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
    ) -> SubagentDiscoveryActivationStageDecision:
        return self.stage_policy_source.evaluate_activation(ticket=ticket)

    def build_manual_activation(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        activation_target: SubagentDiscoveryActivationTarget,
        activated_by: str,
        reason: str | None = None,
    ) -> SubagentDiscoveryActivationStageDecision:
        return self.stage_policy_source.build_manual_activation(
            ticket=ticket,
            activation_target=activation_target,
            activated_by=activated_by,
            reason=reason,
        )

    def resolve_activation_target(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        candidate_subagent_type: str | None,
        delegate_subagent_type: str | None,
        registration_template_id: str | None,
        registration_type: str | None,
        skill_name: str | None,
        snapshot_type: str | None,
        source_id: str | None,
        tool_name: str | None,
        endpoint_path: str | None,
        http_method: str | None,
        openapi_spec_uri: str | None,
        openapi_operation_id: str | None,
        timeout_seconds: float | None,
        runtime_packaging,
        error_factory,
    ) -> SubagentDiscoveryActivationTarget:
        candidate_entry = self.select_activation_candidate_entry(
            ticket=ticket,
            candidate_subagent_type=candidate_subagent_type,
            error_factory=error_factory,
        )
        runtime_registration = runtime_packaging.resolve_runtime_registration(
            candidate_entry=candidate_entry,
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
        )
        normalized_delegate_subagent_type = str(delegate_subagent_type or "").strip() or None
        if bool(normalized_delegate_subagent_type) == bool(runtime_registration):
            raise error_factory(
                "activation requires exactly one of delegate_subagent_type or runtime registration"
            )
        return SubagentDiscoveryActivationTarget(
            candidate_entry=candidate_entry,
            delegate_subagent_type=normalized_delegate_subagent_type,
            runtime_registration=runtime_registration,
        )

    @staticmethod
    def materialize_activation_decision(
        *,
        activation_decision: SubagentDiscoveryActivationStageDecision,
        activation_target: SubagentDiscoveryActivationTarget,
        activated_by: str | None = None,
        reason: str | None = None,
    ) -> SubagentDiscoveryActivationStageDecision:
        return SubagentDiscoveryActivationStageDecision(
            ticket_id=activation_decision.ticket_id,
            intent=activation_decision.intent,
            stage=activation_decision.stage,
            capability=activation_decision.capability,
            policy_source=activation_decision.policy_source,
            activate=activation_decision.activate,
            candidate_subagent_type=str(activation_target.candidate_entry.subagent_type or "").strip() or None,
            registration_template_id=(
                str(activation_target.runtime_registration.registration_template_id or "").strip() or None
                if activation_target.runtime_registration is not None
                else activation_decision.registration_template_id
            ),
            delegate_subagent_type=activation_target.delegate_subagent_type,
            runtime_registration=activation_target.runtime_registration,
            activated_by=(
                str(activated_by or "").strip()
                or activation_decision.activated_by
            ),
            reason=str(reason or "").strip() or activation_decision.reason,
            notes=list(activation_decision.notes or []),
        )

    @staticmethod
    def apply_candidate_selection(
        *,
        ticket: SubagentDiscoveryTicket,
        candidate_result: SubagentDiscoveryCandidateStageResult,
    ) -> None:
        ticket.candidate_catalog_entries = list(candidate_result.candidate_entries)
        ticket.candidate_recommendation = candidate_result.recommendation
        ticket.candidate_shadow_recommendation = candidate_result.shadow_recommendation
        ticket.notes = list(candidate_result.preview_notes)

    @staticmethod
    def apply_review(
        *,
        ticket: SubagentDiscoveryTicket,
        review_decision: SubagentDiscoveryReviewStageDecision,
    ) -> str:
        previous_status = str(ticket.status or "pending").strip() or "pending"
        reviewed_at = datetime.now(timezone.utc)
        ticket.status = review_decision.decision
        ticket.reviewer = review_decision.reviewer
        ticket.review_reason = review_decision.reason
        ticket.reviewed_at = reviewed_at
        ticket.updated_at = reviewed_at
        review_history = list(ticket.review_history or [])
        review_history.append(
            SubagentDiscoveryReviewEvent(
                decision=ticket.status,
                reviewer=review_decision.reviewer,
                reason=review_decision.reason,
                reviewed_at=reviewed_at,
                previous_status=previous_status,  # type: ignore[arg-type]
                source=review_decision.source,
            )
        )
        ticket.review_history = review_history
        notes = list(ticket.notes or [])
        for note in list(review_decision.notes or []):
            if note and note not in notes:
                notes.append(note)
        ticket.notes = notes
        return previous_status

    @staticmethod
    def apply_activation(
        *,
        ticket: SubagentDiscoveryTicket,
        activation_decision: SubagentDiscoveryActivationStageDecision,
        candidate_entry: SubagentCatalogEntry,
    ) -> tuple[SubagentActivationEntry | None, bool]:
        requested_type = str(activation_decision.candidate_subagent_type or "").strip()
        for existing in list(ticket.activated_entries or []):
            if str(existing.subagent_type).strip() == requested_type:
                return existing, False
        activation_entry = SubagentActivationEntry(
            ticket_id=ticket.ticket_id,
            subagent_type=str(candidate_entry.subagent_type).strip(),
            delegate_subagent_type=activation_decision.delegate_subagent_type,
            runtime_registration=activation_decision.runtime_registration,
            catalog_entry=candidate_entry,
            activated_by=activation_decision.activated_by,
            activation_reason=activation_decision.reason,
            notes=list(activation_decision.notes or []),
        )
        ticket.activated_entries.append(activation_entry)
        ticket.updated_at = datetime.now(timezone.utc)
        return activation_entry, True

    def _normalize_candidate_entry(
        self,
        candidate_entry: SubagentCatalogEntry,
    ) -> SubagentCatalogEntry:
        runtime_packaging = getattr(self.discovery_candidate_support, "runtime_packaging", None)
        normalize = getattr(runtime_packaging, "normalize_candidate_entry", None)
        if callable(normalize):
            return normalize(candidate_entry)
        return candidate_entry

    @staticmethod
    def _placeholder_candidate_entry(ticket: SubagentDiscoveryTicket) -> SubagentCatalogEntry:
        gap = ticket.capability_gap
        candidate_type = str(gap.preferred_subagent_type or "").strip()
        if not candidate_type:
            stage = str(gap.stage or "phase").strip() or "phase"
            capability = str(gap.capability or "capability").strip() or "capability"
            candidate_type = f"candidate_{stage}_{capability}".replace("-", "_").replace(" ", "_")
        summary = f"Approved discovery candidate for capability {gap.capability}"
        suitable_intents = [str(gap.intent).strip()] if str(gap.intent or "").strip() else []
        return SubagentCatalogEntry(
            subagent_type=candidate_type,
            summary=summary,
            capabilities=[str(gap.capability).strip()],
            stage=str(gap.stage or "").strip() or None,
            cost_class="medium",
            latency_class="medium",
            side_effect_level="none",
            suitable_intents=suitable_intents,
            tags=["approved_discovery_candidate", "pending_runtime_registration"],
            requires_llm=bool(gap.requires_llm),
            allow_parallel=False,
            max_concurrency=None,
            fallback_subagent_type=None,
            error_fallback_subagent_type=None,
            input_schema_name=None,
            output_schema_name=None,
            input_json_schema=None,
            output_json_schema=None,
        )

    @classmethod
    def _selected_protocol(cls, *, ticket: SubagentDiscoveryTicket) -> str | None:
        recommendation = ticket.candidate_recommendation
        protocol = str(getattr(recommendation, "source_protocol", "") or "").strip()
        if protocol:
            return protocol
        candidate_entry = cls._recommended_entry(ticket=ticket)
        if candidate_entry is None:
            return None
        return cls._entry_protocol(candidate_entry)

    @staticmethod
    def _recommended_entry(*, ticket: SubagentDiscoveryTicket) -> SubagentCatalogEntry | None:
        recommendation = ticket.candidate_recommendation
        requested = str(getattr(recommendation, "candidate_subagent_type", "") or "").strip()
        if requested:
            for entry in list(ticket.candidate_catalog_entries or []):
                if str(entry.subagent_type).strip() == requested:
                    return entry
        entries = list(ticket.candidate_catalog_entries or [])
        if len(entries) == 1:
            return entries[0]
        return None

    @classmethod
    def select_activation_candidate_entry(
        cls,
        *,
        ticket: SubagentDiscoveryTicket,
        candidate_subagent_type: str | None,
        error_factory,
    ) -> SubagentCatalogEntry:
        entries = list(ticket.candidate_catalog_entries or [])
        if not entries:
            raise error_factory(
                f"subagent discovery ticket has no candidate catalog entries: {ticket.ticket_id}"
            )
        requested = str(candidate_subagent_type or "").strip()
        if requested:
            for entry in entries:
                if str(entry.subagent_type).strip() == requested:
                    return entry
            raise error_factory(
                f"candidate catalog entry not found for activation: {requested}"
            )
        if len(entries) == 1:
            return entries[0]
        recommended_entry = cls._recommended_entry(ticket=ticket)
        if recommended_entry is not None:
            return recommended_entry
        raise error_factory(
            f"multiple candidate catalog entries available; candidate_subagent_type is required for {ticket.ticket_id}"
        )

    @staticmethod
    def _entry_protocol(entry: SubagentCatalogEntry) -> str | None:
        tags = [str(tag).strip() for tag in list(entry.tags or []) if str(tag).strip()]
        for tag in tags:
            if tag.startswith("mcp_source:"):
                return "mcp"
            if tag.startswith("openapi_source:"):
                return "openapi"
            if tag.startswith("skill_registration:"):
                return "skill"
            if tag.startswith("http_json_source:"):
                return "http_json"
        return None


__all__ = [
    "SubagentDiscoveryActivationTarget",
    "SubagentDiscoveryActivationStageDecision",
    "SubagentDiscoveryCandidateStageDecision",
    "SubagentDiscoveryCandidateStageResult",
    "SubagentDiscoveryDecisionSource",
    "SubagentDiscoveryProtocolStageDecision",
    "SubagentDiscoveryReviewStageDecision",
]
