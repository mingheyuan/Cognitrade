from __future__ import annotations

import unittest

from research_system.agents._subagent_capability_gap_controller import (
    SubagentCapabilityGapController,
)
from research_system.agents._subagent_discovery_controller import (
    SubagentDiscoveryResolution,
)
from research_system.agents._subagent_phase_controller import SubagentCapabilityGapError
from research_system.schemas.subagents import (
    SubagentCapabilityGap,
    SubagentCatalogEntry,
    SubagentToolDiscoveryRequest,
)


class _DiscoveryControllerStub:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.calls = []

    def resolve_runtime_gap(self, *, gap, store=None):
        self.calls.append((gap, store))
        return self.callback(gap, store)


class SubagentCapabilityGapControllerTests(unittest.TestCase):
    def test_returns_existing_catalog_entry_without_discovery(self) -> None:
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
        discovery_controller = _DiscoveryControllerStub(
            lambda gap, store: SubagentDiscoveryResolution(
                tool_discovery_request=SubagentToolDiscoveryRequest(
                    intent=gap.intent,
                    stage=gap.stage,
                    capability=gap.capability,
                    search_query="unused",
                    reason="unused",
                ),
                ticket=None,
            )
        )
        controller = SubagentCapabilityGapController(
            candidates_resolver=lambda **_: [fallback_entry, preferred_entry],
            entry_selector=lambda candidates, preferred_subagent_type=None: next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.subagent_type == preferred_subagent_type
                ),
                candidates[0],
            ),
            capability_gap_builder=lambda **kwargs: SubagentCapabilityGap(
                intent=kwargs["intent"],
                stage=kwargs["stage"],
                capability=kwargs["capability"],
                preferred_subagent_type=kwargs["preferred_subagent_type"],
                requires_llm=kwargs["requires_llm"],
                reason="missing capability",
            ),
            discovery_controller=discovery_controller,
            error_factory=SubagentCapabilityGapError,
        )

        selected = controller.select_catalog_entry(
            stage="review",
            intent="review_thesis",
            capability="review_strategy",
            preferred_subagent_type="preferred_review",
        )

        self.assertEqual(selected.subagent_type, "preferred_review")
        self.assertEqual(discovery_controller.calls, [])

    def test_resolves_missing_capability_through_discovery_and_catalog_retry(self) -> None:
        discovered_entry = SubagentCatalogEntry(
            subagent_type="candidate_collect_market_snapshot_skill",
            capabilities=["market_signal_extension"],
            stage="collect",
            suitable_intents=["stock_check"],
        )
        request = SubagentToolDiscoveryRequest(
            intent="stock_check",
            stage="collect",
            capability="market_signal_extension",
            search_query="stock_check collect market_signal_extension",
            preferred_protocols=["skill", "mcp"],
            reason="missing market signal extension capability",
        )
        state = {"discovered": False}
        recorded_rows = []

        def resolve_candidates(**_kwargs):
            if state["discovered"]:
                return [discovered_entry]
            return []

        def resolve_gap(gap, store):
            state["discovered"] = True
            return SubagentDiscoveryResolution(
                tool_discovery_request=request,
                ticket={"ticket_id": "ticket-1", "store": store},
            )

        controller = SubagentCapabilityGapController(
            candidates_resolver=resolve_candidates,
            entry_selector=lambda candidates, preferred_subagent_type=None: candidates[0],
            capability_gap_builder=lambda **kwargs: SubagentCapabilityGap(
                intent=kwargs["intent"],
                stage=kwargs["stage"],
                capability=kwargs["capability"],
                preferred_subagent_type=kwargs["preferred_subagent_type"],
                requires_llm=kwargs["requires_llm"],
                reason="missing market signal extension capability",
            ),
            discovery_controller=_DiscoveryControllerStub(resolve_gap),
            discovery_store_resolver=lambda: "store-1",
            record_capability_gap=lambda **kwargs: recorded_rows.append(
                ("gap", kwargs["gap"].capability)
            ),
            record_tool_discovery_request=lambda **kwargs: recorded_rows.append(
                ("request", kwargs["request"].search_query)
            ),
            record_tool_discovery_auto_activation=lambda **kwargs: recorded_rows.append(
                ("activation", kwargs["ticket"]["ticket_id"])
            ),
            error_factory=SubagentCapabilityGapError,
        )

        resolution = controller.resolve_catalog_entry(
            stage="collect",
            intent="stock_check",
            capability="market_signal_extension",
        )

        self.assertIsNotNone(resolution.capability_gap_resolution)
        self.assertEqual(
            resolution.capability_gap_resolution.tool_discovery_request.preferred_protocols,
            ["skill", "mcp"],
        )
        self.assertEqual(resolution.selected_entry.subagent_type, discovered_entry.subagent_type)
        self.assertEqual(
            recorded_rows,
            [
                ("gap", "market_signal_extension"),
                ("request", "stock_check collect market_signal_extension"),
                ("activation", "ticket-1"),
            ],
        )

    def test_raises_capability_gap_error_when_discovery_does_not_restore_catalog_entry(self) -> None:
        request = SubagentToolDiscoveryRequest(
            intent="review_thesis",
            stage="review",
            capability="missing_capability",
            search_query="review_thesis review missing_capability",
            reason="missing capability",
        )
        controller = SubagentCapabilityGapController(
            candidates_resolver=lambda **_: [],
            entry_selector=lambda candidates, preferred_subagent_type=None: candidates[0],
            capability_gap_builder=lambda **kwargs: SubagentCapabilityGap(
                intent=kwargs["intent"],
                stage=kwargs["stage"],
                capability=kwargs["capability"],
                preferred_subagent_type=kwargs["preferred_subagent_type"],
                requires_llm=kwargs["requires_llm"],
                reason="missing capability",
            ),
            discovery_controller=_DiscoveryControllerStub(
                lambda gap, store: SubagentDiscoveryResolution(
                    tool_discovery_request=request,
                    ticket=None,
                )
            ),
            error_factory=SubagentCapabilityGapError,
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
