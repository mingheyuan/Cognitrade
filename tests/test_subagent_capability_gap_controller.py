from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.agents._subagent_capability_gap_controller import (
    SubagentCapabilityGapController,
)
from research_system.agents._subagent_control_escalation import (
    SubagentControlEscalationError,
    SubagentControlEscalationPolicyService,
    SubagentControlEscalationRule,
)
from research_system.agents._subagent_discovery_controller import (
    SubagentDiscoveryResolution,
)
from research_system.agents._subagent_phase_controller import SubagentCapabilityGapError
from research_system.schemas.planning import ActionPlanPatch, ActionPlanPatchOperation
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

        resolution = controller.resolve_capability_gap(
            stage="collect",
            intent="stock_check",
            capability="market_signal_extension",
        )

        self.assertEqual(
            resolution.tool_discovery_request.preferred_protocols,
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

        resolution = controller.resolve_capability_gap(
            stage="review",
            intent="review_thesis",
            capability="missing_capability",
        )
        error = controller.build_unresolved_gap_error(resolution)

        self.assertIsInstance(error, SubagentCapabilityGapError)
        self.assertEqual(error.gap.capability, "missing_capability")
        self.assertEqual(
            error.tool_discovery_request.search_query,
            "review_thesis review missing_capability",
        )

    def test_raises_action_replan_escalation_when_policy_requests_it(self) -> None:
        recorded_rows = []
        discovery_controller = _DiscoveryControllerStub(
            lambda gap, store: self.fail("runtime discovery should not run during action replan escalation")
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
                reason="missing review capability",
            ),
            discovery_controller=discovery_controller,
            escalation_policy=SubagentControlEscalationPolicyService(
                default_strategy="discovery",
                rules=[
                    SubagentControlEscalationRule(
                        strategy="action_replan",
                        rule_id="review_optional_phase",
                        intent="review_thesis",
                        stage="review",
                        notes=("skip_optional_review_phase",),
                    )
                ],
            ),
            record_capability_gap=lambda **kwargs: recorded_rows.append(("gap", kwargs["gap"].capability)),
            record_escalation_decision=lambda **kwargs: recorded_rows.append(
                ("escalation", kwargs["escalation_decision"].strategy)
            ),
            error_factory=SubagentCapabilityGapError,
        )

        with self.assertRaises(SubagentControlEscalationError) as exc_info:
            controller.resolve_capability_gap(
                stage="review",
                intent="review_thesis",
                capability="missing_capability",
            )

        self.assertEqual(discovery_controller.calls, [])
        self.assertEqual(exc_info.exception.gap.capability, "missing_capability")
        self.assertEqual(exc_info.exception.escalation_decision.strategy, "action_replan")
        self.assertEqual(exc_info.exception.plan_patch.decision_type, "replan")
        self.assertEqual(exc_info.exception.plan_patch.operations[0].op_type, "request_replan")
        self.assertEqual(
            exc_info.exception.plan_patch.operations[0].metadata["control_source"],
            "subagent_capability_gap",
        )
        self.assertEqual(recorded_rows, [("gap", "missing_capability"), ("escalation", "action_replan")])

    def test_repeat_action_replan_guard_falls_back_to_discovery(self) -> None:
        request = SubagentToolDiscoveryRequest(
            intent="review_thesis",
            stage="review",
            capability="missing_capability",
            search_query="review_thesis review missing_capability",
            reason="missing capability",
        )
        discovery_controller = _DiscoveryControllerStub(
            lambda gap, store: SubagentDiscoveryResolution(
                tool_discovery_request=request,
                ticket=None,
            )
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
            discovery_controller=discovery_controller,
            escalation_policy=SubagentControlEscalationPolicyService(
                default_strategy="discovery",
                rules=[
                    SubagentControlEscalationRule(
                        strategy="action_replan",
                        rule_id="review_optional_phase",
                        intent="review_thesis",
                        stage="review",
                    )
                ],
            ),
            runtime_binding_getter=lambda: {
                "context": SimpleNamespace(
                    action_plan_patches=[
                        ActionPlanPatch(
                            decision_type="replan",
                            operations=[
                                ActionPlanPatchOperation(
                                    op_type="request_replan",
                                    reason="review capability missing",
                                    metadata={
                                        "control_source": "subagent_capability_gap",
                                        "intent": "review_thesis",
                                        "stage": "review",
                                        "capability": "missing_capability",
                                    },
                                )
                            ],
                            notes=["request_action_plan_replan"],
                            termination_reason="review capability missing",
                        ).model_dump(mode="json")
                    ]
                )
            },
            error_factory=SubagentCapabilityGapError,
        )

        resolution = controller.resolve_capability_gap(
            stage="review",
            intent="review_thesis",
            capability="missing_capability",
        )

        self.assertEqual(len(discovery_controller.calls), 1)
        self.assertIsNone(resolution.selected_entry)
        self.assertEqual(resolution.escalation_decision.strategy, "discovery")
        self.assertIn(
            "action_replan_guarded_repeat_capability_gap",
            resolution.escalation_decision.notes,
        )

    def test_can_delegate_capability_gap_escalation_to_runtime_control(self) -> None:
        delegated_calls = []
        discovery_controller = _DiscoveryControllerStub(
            lambda gap, store: self.fail("runtime discovery should not run during action replan escalation")
        )

        class _RuntimeControlStub:
            def resolve_subagent_capability_gap_escalation(self, **kwargs):
                delegated_calls.append(dict(kwargs))
                return kwargs["escalation_controller"].resolve(
                    gap=kwargs["gap"],
                    preferred_subagent_type=kwargs["preferred_subagent_type"],
                    requires_llm=kwargs["requires_llm"],
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
                reason="missing review capability",
            ),
            discovery_controller=discovery_controller,
            escalation_policy=SubagentControlEscalationPolicyService(
                default_strategy="discovery",
                rules=[
                    SubagentControlEscalationRule(
                        strategy="action_replan",
                        rule_id="review_optional_phase",
                        intent="review_thesis",
                        stage="review",
                    )
                ],
            ),
            runtime_binding_getter=lambda: {
                "runtime_control": _RuntimeControlStub(),
                "request_id": "req-gap-runtime-control",
                "intent": "review_thesis",
            },
            error_factory=SubagentCapabilityGapError,
        )

        with self.assertRaises(SubagentControlEscalationError) as exc_info:
            controller.resolve_capability_gap(
                stage="review",
                intent="review_thesis",
                capability="missing_capability",
            )

        self.assertEqual(len(delegated_calls), 1)
        self.assertEqual(delegated_calls[0]["gap"].capability, "missing_capability")
        self.assertEqual(exc_info.exception.escalation_decision.strategy, "action_replan")
        self.assertEqual(discovery_controller.calls, [])


if __name__ == "__main__":
    unittest.main()
