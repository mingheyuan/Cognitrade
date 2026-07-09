from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.execution.core.runtime_subagent_transition_controller import (
    OrchestratorRuntimeSubagentTransitionController,
)


class _HostStub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def _emit_event(self, context, **kwargs) -> None:
        self.events.append(dict(kwargs))


class RuntimeSubagentTransitionControllerTests(unittest.TestCase):
    def _build_binding(self, context):
        return {
            "context": context,
            "request_id": "req-subagent-transition",
            "intent": "review_thesis",
            "agent": "reviewer",
        }

    def test_catalog_resolution_records_continue_discovery_transition(self) -> None:
        host = _HostStub()
        controller = OrchestratorRuntimeSubagentTransitionController(host)
        context = SimpleNamespace(runtime_control_stage_decisions=[], dynamic_trace=[])

        controller.handle_catalog_resolution(
            runtime_binding=self._build_binding(context),
            stage="review",
            intent="review_thesis",
            capability="review_strategy",
            preferred_subagent_type=None,
            requires_llm=None,
            resolution=SimpleNamespace(
                selected_entry=SimpleNamespace(subagent_type="candidate_review_bridge"),
                capability_gap_resolution=SimpleNamespace(
                    ticket=SimpleNamespace(ticket_id="ticket-review-1"),
                    escalation_decision=SimpleNamespace(
                        strategy="discovery",
                        policy_source="rule",
                        notes=("runtime_control_subagent_recovered_via_discovery",),
                    ),
                ),
            ),
        )

        self.assertEqual(
            [row["event"] for row in context.runtime_control_stage_decisions],
            ["runtime_control_subagent_transition_decided"],
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["transition_type"],
            "continue_discovery",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["selected_subagent_type"],
            "candidate_review_bridge",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["discovery_ticket_id"],
            "ticket-review-1",
        )
        self.assertEqual(
            [event["event_type"] for event in host.events],
            ["runtime_control_subagent_transition_decided"],
        )

    def test_phase_resolution_records_apply_local_replan_transition(self) -> None:
        host = _HostStub()
        controller = OrchestratorRuntimeSubagentTransitionController(host)
        context = SimpleNamespace(runtime_control_stage_decisions=[], dynamic_trace=[])

        controller.handle_phase_resolution(
            runtime_binding=self._build_binding(context),
            plan=SimpleNamespace(
                intent="review_thesis",
                phase="review",
                plan_id="plan-1",
            ),
            dag_id="review_execution",
            resolution=SimpleNamespace(
                final_patch=SimpleNamespace(
                    decision_type="replan",
                    operations=[SimpleNamespace(op_type="request_replan")],
                    policy_source="rule",
                    notes=["request_subagent_replan"],
                ),
                final_patch_rows=[{"patch_id": "patch-review"}],
                rule_variant_id="rule_replanned",
                final_plan_variant="rule_replanned",
            ),
        )

        self.assertEqual(
            context.runtime_control_stage_decisions[0]["transition_source"],
            "phase_patch_cycle",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["transition_type"],
            "apply_local_replan",
        )
        self.assertTrue(context.runtime_control_stage_decisions[0]["final_request_replan"])
        self.assertEqual(context.runtime_control_stage_decisions[0]["patch_count"], 1)

    def test_capability_gap_escalation_records_request_action_replan_transition(self) -> None:
        host = _HostStub()
        controller = OrchestratorRuntimeSubagentTransitionController(host)
        context = SimpleNamespace(runtime_control_stage_decisions=[], dynamic_trace=[])

        controller.handle_capability_gap_escalation(
            runtime_binding=self._build_binding(context),
            gap=SimpleNamespace(
                intent="review_thesis",
                stage="review",
                capability="missing_review_capability",
            ),
            preferred_subagent_type=None,
            requires_llm=None,
            escalation_decision=SimpleNamespace(
                strategy="action_replan",
                policy_source="rule",
                notes=("skip_optional_review_phase",),
            ),
        )

        self.assertEqual(
            context.runtime_control_stage_decisions[0]["transition_source"],
            "capability_gap_escalation",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["transition_type"],
            "request_action_replan",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["capability"],
            "missing_review_capability",
        )


if __name__ == "__main__":
    unittest.main()
