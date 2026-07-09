from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from research_system.agents._subagent_capability_gap_controller import (
    SubagentCapabilityGapController,
    SubagentCapabilityGapResolution,
)
from research_system.schemas.subagents import SubagentCatalogEntry


@dataclass(frozen=True)
class SubagentCatalogStageDecision:
    intent: str | None
    stage: str | None
    capability: str
    preferred_subagent_type: str | None
    requires_llm: bool | None
    decision_type: Literal["selected", "escalate_capability_gap"]
    policy_source: Literal["rule"] = "rule"
    candidate_subagent_types: list[str] = field(default_factory=list)
    selected_subagent_type: str | None = None
    selection_source: Literal["catalog", "capability_gap_discovery"] | None = None
    reason: str = "select_catalog_candidate"
    notes: list[str] = field(default_factory=list)

    def event_payload(self) -> dict[str, object]:
        return {
            "decision_stage": "catalog_selection",
            "intent": self.intent,
            "stage": self.stage,
            "capability": self.capability,
            "preferred_subagent_type": self.preferred_subagent_type,
            "requires_llm": self.requires_llm,
            "policy_source": self.policy_source,
            "decision_type": self.decision_type,
            "candidate_subagent_types": list(self.candidate_subagent_types),
            "selected_subagent_type": self.selected_subagent_type,
            "selection_source": self.selection_source,
            "reason": self.reason,
            "notes": list(self.notes),
        }


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
        runtime_binding_getter=None,
        emit_event=None,
        service_name: str | None = None,
    ) -> None:
        self.candidates_resolver = candidates_resolver
        self.entry_selector = entry_selector
        self.capability_gap_controller = capability_gap_controller
        self.runtime_binding_getter = runtime_binding_getter
        self.emit_event = emit_event
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
        binding = (
            dict(self.runtime_binding_getter() or {})
            if callable(self.runtime_binding_getter)
            else {}
        )
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or decision.intent or "").strip()
        phase = str(binding.get("phase") or decision.stage or "").strip() or None
        agent = str(binding.get("agent") or self.service_name).strip() or self.service_name
        if context is None or support is None or not request_id or not intent:
            return
        payload = {
            "event": "subagent_catalog_selection_decided",
            **decision.event_payload(),
            "agent": agent,
        }
        decision_rows = getattr(context, "subagent_catalog_stage_decisions", None)
        if not isinstance(decision_rows, list):
            try:
                setattr(context, "subagent_catalog_stage_decisions", [])
            except Exception:
                decision_rows = None
            else:
                decision_rows = getattr(context, "subagent_catalog_stage_decisions", None)
        if isinstance(decision_rows, list):
            decision_rows.append(dict(payload))
        control_rows = getattr(context, "subagent_control_stage_decisions", None)
        if not isinstance(control_rows, list):
            try:
                setattr(context, "subagent_control_stage_decisions", [])
            except Exception:
                control_rows = None
            else:
                control_rows = getattr(context, "subagent_control_stage_decisions", None)
        if isinstance(control_rows, list):
            control_rows.append(
                {
                    **dict(payload),
                    "control_stage": "catalog_selection",
                }
            )
        dynamic_trace = getattr(context, "dynamic_trace", None)
        if isinstance(dynamic_trace, list):
            dynamic_trace.append(dict(payload))
        message = (
            f"subagent_catalog_selection_decided resolved {decision.decision_type} "
            f"for capability {decision.capability}"
        )
        if callable(self.emit_event):
            self.emit_event(
                event_type="subagent_catalog_selection_decided",
                message=message,
                payload=dict(payload),
                phase=phase,
                agent=agent,
            )
            return
        support.emit_event(
            context,
            request_id=request_id,
            intent=intent,
            event_type="subagent_catalog_selection_decided",
            phase=phase,
            agent=agent,
            message=message,
            payload=dict(payload),
        )


__all__ = [
    "SubagentCatalogController",
    "SubagentCatalogSelectionResolution",
    "SubagentCatalogStageDecision",
]
