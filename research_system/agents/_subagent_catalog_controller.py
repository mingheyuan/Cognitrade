from __future__ import annotations

from dataclasses import dataclass, field

from research_system.agents._subagent_capability_gap_controller import (
    SubagentCapabilityGapController,
    SubagentCapabilityGapResolution,
)
from research_system.runtime.orchestration._subagent_control_stage_recorder import (
    SubagentControlStageRecorder,
)
from research_system.runtime.orchestration._runtime_control_stage_decisions import (
    SubagentCatalogStageDecision,
)
from research_system.schemas.subagents import SubagentCatalogEntry


@dataclass(frozen=True)
class SubagentCatalogSelectionResolution:
    selected_entry: SubagentCatalogEntry | None = None
    capability_gap_resolution: SubagentCapabilityGapResolution | None = None
    stage_decisions: list[SubagentCatalogStageDecision] = field(default_factory=list)


class SubagentCatalogController:
    def __init__(
        self,
        *,
        candidates_resolver,
        entry_selector,
        capability_gap_controller: SubagentCapabilityGapController,
        control_stage_recorder=None,
        runtime_binding_getter=None,
        emit_event=None,
        service_name: str | None = None,
    ) -> None:
        self.candidates_resolver = candidates_resolver
        self.entry_selector = entry_selector
        self.capability_gap_controller = capability_gap_controller
        self.control_stage_recorder = control_stage_recorder or SubagentControlStageRecorder(
            runtime_binding_getter=runtime_binding_getter,
            emit_event=emit_event,
        )
        self.service_name = service_name or "subagent_catalog"

    def resolve_catalog_entry(
        self,
        *,
        stage: str,
        intent: str,
        capability: str,
        preferred_subagent_type: str | None = None,
        requires_llm: bool | None = None,
    ) -> SubagentCatalogSelectionResolution:
        candidates = self.candidates_resolver(
            stage=stage,
            intent=intent,
            capability=capability,
            requires_llm=requires_llm,
        )
        if candidates:
            selected_entry = self.entry_selector(
                candidates,
                preferred_subagent_type=preferred_subagent_type,
            )
            stage_decision = SubagentCatalogStageDecision(
                intent=intent,
                stage=stage,
                capability=capability,
                preferred_subagent_type=preferred_subagent_type,
                requires_llm=requires_llm,
                decision_type="selected",
                candidate_subagent_types=[candidate.subagent_type for candidate in candidates],
                selected_subagent_type=selected_entry.subagent_type,
                selection_source="catalog",
                reason="select_catalog_candidate",
            )
            self._record_stage_decision(stage_decision)
            return SubagentCatalogSelectionResolution(
                selected_entry=selected_entry,
                stage_decisions=[stage_decision],
            )
        escalate_decision = SubagentCatalogStageDecision(
            intent=intent,
            stage=stage,
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
            decision_type="escalate_capability_gap",
            candidate_subagent_types=[],
            selected_subagent_type=None,
            selection_source=None,
            reason="catalog_capability_gap",
        )
        self._record_stage_decision(escalate_decision)
        capability_gap_resolution = self.capability_gap_controller.resolve_capability_gap(
            stage=stage,
            intent=intent,
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
        )
        stage_decisions = [escalate_decision]
        selected_entry = capability_gap_resolution.selected_entry
        if selected_entry is not None:
            resolved_candidates = self.candidates_resolver(
                stage=stage,
                intent=intent,
                capability=capability,
                requires_llm=requires_llm,
            )
            recovery_decision = SubagentCatalogStageDecision(
                intent=intent,
                stage=stage,
                capability=capability,
                preferred_subagent_type=preferred_subagent_type,
                requires_llm=requires_llm,
                decision_type="selected",
                candidate_subagent_types=[
                    candidate.subagent_type for candidate in resolved_candidates
                ],
                selected_subagent_type=selected_entry.subagent_type,
                selection_source="capability_gap_discovery",
                reason="select_discovery_activated_catalog_candidate",
                notes=["catalog_recovered_from_capability_gap_discovery"],
            )
            self._record_stage_decision(recovery_decision)
            stage_decisions.append(recovery_decision)
        return SubagentCatalogSelectionResolution(
            selected_entry=selected_entry,
            capability_gap_resolution=capability_gap_resolution,
            stage_decisions=stage_decisions,
        )

    def select_catalog_entry(
        self,
        *,
        stage: str,
        intent: str,
        capability: str,
        preferred_subagent_type: str | None = None,
        requires_llm: bool | None = None,
    ) -> SubagentCatalogEntry:
        resolution = self.resolve_catalog_entry(
            stage=stage,
            intent=intent,
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
        )
        if resolution.selected_entry is not None:
            return resolution.selected_entry
        raise self.capability_gap_controller.build_unresolved_gap_error(
            resolution.capability_gap_resolution
        )

    def _record_stage_decision(self, decision: SubagentCatalogStageDecision) -> None:
        if self.control_stage_recorder is None:
            return
        self.control_stage_recorder.record(
            event_type=decision.event_type,
            message=decision.message(),
            payload=decision.event_payload(),
            phase=str(decision.stage or "").strip() or None,
            agent=self.service_name,
            extra_context_attrs=["subagent_catalog_stage_decisions"],
        )


__all__ = [
    "SubagentCatalogController",
    "SubagentCatalogSelectionResolution",
    "SubagentCatalogStageDecision",
]
