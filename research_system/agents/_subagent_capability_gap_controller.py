from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_system.agents._subagent_discovery_controller import SubagentDiscoveryController
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


class SubagentCapabilityGapController:
    def __init__(
        self,
        *,
        capability_gap_builder,
        discovery_controller: SubagentDiscoveryController,
        candidates_resolver,
        entry_selector,
        discovery_store_resolver=None,
        record_capability_gap=None,
        record_tool_discovery_request=None,
        record_tool_discovery_auto_activation=None,
        error_factory=RuntimeError,
    ) -> None:
        self.capability_gap_builder = capability_gap_builder
        self.discovery_controller = discovery_controller
        self.candidates_resolver = candidates_resolver
        self.entry_selector = entry_selector
        self.discovery_store_resolver = discovery_store_resolver
        self.record_capability_gap = record_capability_gap
        self.record_tool_discovery_request = record_tool_discovery_request
        self.record_tool_discovery_auto_activation = record_tool_discovery_auto_activation
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
