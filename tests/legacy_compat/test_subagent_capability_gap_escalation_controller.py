from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.agents._subagent_capability_gap_escalation_controller import (
    SubagentCapabilityGapEscalationController,
)
from research_system.agents._subagent_control_escalation import (
    SubagentControlEscalationPolicyService,
    SubagentControlEscalationRule,
)
from research_system.schemas.planning import ActionPlanPatch, ActionPlanPatchOperation
from research_system.schemas.subagents import SubagentCapabilityGap


class SubagentCapabilityGapEscalationControllerTests(unittest.TestCase):
    @staticmethod
    def _gap() -> SubagentCapabilityGap:
        return SubagentCapabilityGap(
            intent="review_thesis",
            stage="review",
            capability="missing_review_capability",
            preferred_subagent_type=None,
            requires_llm=None,
            reason="missing review capability",
        )

    def test_resolve_local_defaults_to_discovery_without_policy(self) -> None:
        controller = SubagentCapabilityGapEscalationController()

        decision = controller.resolve_local(
            gap=self._gap(),
            preferred_subagent_type=None,
            requires_llm=None,
        )

        self.assertEqual(decision.strategy, "discovery")
        self.assertIn("subagent_control_escalation_rule=default", decision.notes)

    def test_resolve_local_guards_repeat_action_replan(self) -> None:
        controller = SubagentCapabilityGapEscalationController(
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
                                        "capability": "missing_review_capability",
                                    },
                                )
                            ],
                            notes=["request_action_plan_replan"],
                            termination_reason="review capability missing",
                        ).model_dump(mode="json")
                    ]
                )
            },
        )

        decision = controller.resolve_local(
            gap=self._gap(),
            preferred_subagent_type=None,
            requires_llm=None,
        )

        self.assertEqual(decision.strategy, "discovery")
        self.assertIn("action_replan_guarded_repeat_capability_gap", decision.notes)

    def test_resolve_can_delegate_to_runtime_control(self) -> None:
        delegated_calls = []

        class _RuntimeControlStub:
            def resolve_subagent_capability_gap_escalation(self, **kwargs):
                delegated_calls.append(dict(kwargs))
                return kwargs["escalation_controller"].resolve_local(
                    gap=kwargs["gap"],
                    preferred_subagent_type=kwargs["preferred_subagent_type"],
                    requires_llm=kwargs["requires_llm"],
                )

        controller = SubagentCapabilityGapEscalationController(
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
        )

        decision = controller.resolve(
            gap=self._gap(),
            preferred_subagent_type=None,
            requires_llm=None,
        )

        self.assertEqual(len(delegated_calls), 1)
        self.assertEqual(delegated_calls[0]["gap"].capability, "missing_review_capability")
        self.assertEqual(decision.strategy, "action_replan")


if __name__ == "__main__":
    unittest.main()
