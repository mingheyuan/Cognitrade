from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.agents._subagent_capability_gap_controller import (
    SubagentCapabilityGapController,
    SubagentCapabilityGapResolution,
)
from research_system.agents._subagent_catalog_controller import (
    SubagentCatalogController,
)
from research_system.agents._subagent_phase_controller import SubagentCapabilityGapError
from research_system.schemas.subagents import (
    SubagentCapabilityGap,
    SubagentCatalogEntry,
    SubagentToolDiscoveryRequest,
)


class _CapabilityGapControllerStub:
    def __init__(self, callback, *, error_factory=SubagentCapabilityGapError) -> None:
        self.callback = callback
        self.calls = []
        self.error_factory = error_factory

    def resolve_capability_gap(self, **kwargs):
        self.calls.append(kwargs)
        return self.callback(**kwargs)

    def build_unresolved_gap_error(self, capability_gap_resolution):
        if capability_gap_resolution is None:
            return self.error_factory("unable to resolve catalog entry")
        return self.error_factory(
            gap=capability_gap_resolution.gap,
            tool_discovery_request=capability_gap_resolution.tool_discovery_request,
        )


class SubagentCatalogControllerTests(unittest.TestCase):
    def test_selects_existing_catalog_entry_without_escalation(self) -> None:
        runtime_context = SimpleNamespace(
            subagent_catalog_stage_decisions=[],
            dynamic_trace=[],
        )
        support = SimpleNamespace(emit_event=lambda *_args, **_kwargs: None)
        preferred_entry = SubagentCatalogEntry(
            subagent_type="preferred_review",
            capabilities=["review_strategy"],
            stage="review",
            suitable_intents=["review_thesis"],
        )
        fallback_entry = SubagentCatalogEntry(
            subagent_type="cheap_review",
            capabilities=["review_strategy"],
            stage="review",
            suitable_intents=["review_thesis"],
        )
        gap_controller = _CapabilityGapControllerStub(
            lambda **kwargs: SubagentCapabilityGapResolution(
                gap=SubagentCapabilityGap(
                    intent=kwargs["intent"],
                    stage=kwargs["stage"],
                    capability=kwargs["capability"],
                    reason="unused",
                ),
                tool_discovery_request=SubagentToolDiscoveryRequest(
                    intent=kwargs["intent"],
                    stage=kwargs["stage"],
                    capability=kwargs["capability"],
                    search_query="unused",
                    reason="unused",
                ),
                ticket=None,
                selected_entry=None,
            )
        )
        controller = SubagentCatalogController(
            candidates_resolver=lambda **_: [fallback_entry, preferred_entry],
            entry_selector=lambda candidates, preferred_subagent_type=None: next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.subagent_type == preferred_subagent_type
                ),
                candidates[0],
            ),
            capability_gap_controller=gap_controller,
            runtime_binding_getter=lambda: {
                "context": runtime_context,
                "support": support,
                "request_id": "req-catalog-direct",
                "intent": "review_thesis",
            },
            service_name="reviewer",
        )

        selected = controller.select_catalog_entry(
            stage="review",
            intent="review_thesis",
            capability="review_strategy",
            preferred_subagent_type="preferred_review",
        )

        self.assertEqual(selected.subagent_type, "preferred_review")
        self.assertEqual(gap_controller.calls, [])
        self.assertEqual(len(runtime_context.subagent_catalog_stage_decisions), 1)
        self.assertEqual(
            runtime_context.subagent_catalog_stage_decisions[0]["decision_type"],
            "selected",
        )
        self.assertEqual(
            runtime_context.subagent_catalog_stage_decisions[0]["selection_source"],
            "catalog",
        )

    def test_escalates_capability_gap_and_records_recovery_selection(self) -> None:
        runtime_context = SimpleNamespace(
            subagent_catalog_stage_decisions=[],
            dynamic_trace=[],
        )
        support = SimpleNamespace(emit_event=lambda *_args, **_kwargs: None)
        discovered_entry = SubagentCatalogEntry(
            subagent_type="candidate_collect_market_snapshot_skill",
            capabilities=["market_signal_extension"],
            stage="collect",
            suitable_intents=["stock_check"],
        )
        state = {"discovered": False}

        def resolve_candidates(**_kwargs):
            if state["discovered"]:
                return [discovered_entry]
            return []

        def resolve_gap(**kwargs):
            state["discovered"] = True
            return SubagentCapabilityGapResolution(
                gap=SubagentCapabilityGap(
                    intent=kwargs["intent"],
                    stage=kwargs["stage"],
                    capability=kwargs["capability"],
                    preferred_subagent_type=kwargs["preferred_subagent_type"],
                    requires_llm=kwargs["requires_llm"],
                    reason="missing market signal extension capability",
                ),
                tool_discovery_request=SubagentToolDiscoveryRequest(
                    intent=kwargs["intent"],
                    stage=kwargs["stage"],
                    capability=kwargs["capability"],
                    search_query="stock_check collect market_signal_extension",
                    preferred_protocols=["skill", "mcp"],
                    reason="missing market signal extension capability",
                ),
                ticket={"ticket_id": "ticket-1"},
                selected_entry=discovered_entry,
            )

        controller = SubagentCatalogController(
            candidates_resolver=resolve_candidates,
            entry_selector=lambda candidates, preferred_subagent_type=None: candidates[0],
            capability_gap_controller=_CapabilityGapControllerStub(resolve_gap),
            runtime_binding_getter=lambda: {
                "context": runtime_context,
                "support": support,
                "request_id": "req-catalog-escalate",
                "intent": "stock_check",
            },
            service_name="collector",
        )

        resolution = controller.resolve_catalog_entry(
            stage="collect",
            intent="stock_check",
            capability="market_signal_extension",
        )

        self.assertEqual(resolution.selected_entry.subagent_type, discovered_entry.subagent_type)
        self.assertEqual(
            [row["decision_type"] for row in runtime_context.subagent_catalog_stage_decisions],
            ["escalate_capability_gap", "selected"],
        )
        self.assertEqual(
            runtime_context.subagent_catalog_stage_decisions[1]["selection_source"],
            "capability_gap_discovery",
        )
        self.assertEqual(
            runtime_context.dynamic_trace[0]["event"],
            "subagent_catalog_selection_decided",
        )

    def test_raises_unresolved_gap_error_when_escalation_does_not_recover_catalog(self) -> None:
        request = SubagentToolDiscoveryRequest(
            intent="review_thesis",
            stage="review",
            capability="missing_capability",
            search_query="review_thesis review missing_capability",
            reason="missing capability",
        )
        controller = SubagentCatalogController(
            candidates_resolver=lambda **_: [],
            entry_selector=lambda candidates, preferred_subagent_type=None: candidates[0],
            capability_gap_controller=_CapabilityGapControllerStub(
                lambda **kwargs: SubagentCapabilityGapResolution(
                    gap=SubagentCapabilityGap(
                        intent=kwargs["intent"],
                        stage=kwargs["stage"],
                        capability=kwargs["capability"],
                        preferred_subagent_type=kwargs["preferred_subagent_type"],
                        requires_llm=kwargs["requires_llm"],
                        reason="missing capability",
                    ),
                    tool_discovery_request=request,
                    ticket=None,
                    selected_entry=None,
                )
            ),
        )

        with self.assertRaises(SubagentCapabilityGapError) as exc_info:
            controller.select_catalog_entry(
                stage="review",
                intent="review_thesis",
                capability="missing_capability",
            )

        self.assertEqual(exc_info.exception.gap.capability, "missing_capability")
        self.assertEqual(
            exc_info.exception.tool_discovery_request.search_query,
            "review_thesis review missing_capability",
        )


if __name__ == "__main__":
    unittest.main()
