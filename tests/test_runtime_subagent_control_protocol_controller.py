from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.execution.core.runtime_subagent_control_controller import (
    OrchestratorRuntimeSubagentControlController,
)
from research_system.runtime.execution.core.runtime_subagent_control_protocol_controller import (
    OrchestratorRuntimeSubagentControlProtocolController,
)


class _CatalogControllerStub:
    def __init__(self, resolution) -> None:
        self.resolution = resolution
        self.calls: list[dict] = []
        self.capability_gap_controller = SimpleNamespace(
            build_unresolved_gap_error=lambda resolution: RuntimeError(
                f"unresolved:{getattr(getattr(resolution, 'gap', None), 'capability', 'missing')}"
            )
        )

    def resolve_catalog_entry(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.resolution


class _PhaseExecutionControllerStub:
    def __init__(self, resolution) -> None:
        self.resolution = resolution
        self.calls: list[dict] = []

    def execute_patch_cycle(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.resolution


class _HostStub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def _emit_event(self, context, **kwargs) -> None:
        self.events.append(dict(kwargs))


class RuntimeSubagentControlProtocolControllerTests(unittest.TestCase):
    def _build_binding(self, context):
        return {
            "context": context,
            "request_id": "req-subagent-protocol",
            "intent": "review_thesis",
            "agent": "reviewer",
        }

    def test_resolve_catalog_entry_records_protocol_row_after_runtime_resolution(self) -> None:
        host = _HostStub()
        controller = OrchestratorRuntimeSubagentControlProtocolController(
            host,
            subagent_controller=OrchestratorRuntimeSubagentControlController(host),
        )
        context = SimpleNamespace(runtime_control_stage_decisions=[], dynamic_trace=[])
        resolution = SimpleNamespace(
            selected_entry=SimpleNamespace(subagent_type="cheap_review"),
            capability_gap_resolution=None,
        )

        resolved = controller.resolve_catalog_entry(
            catalog_controller=_CatalogControllerStub(resolution),
            runtime_binding=self._build_binding(context),
            stage="review",
            intent="review_thesis",
            capability="review_strategy",
            preferred_subagent_type="cheap_review",
            requires_llm=False,
        )

        self.assertIs(resolved, resolution)
        self.assertEqual(
            [row["event"] for row in context.runtime_control_stage_decisions],
            [
                "runtime_control_subagent_resolved",
                "runtime_control_subagent_protocol_resolved",
            ],
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["resolution_source"],
            "catalog_selection",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["decision_type"],
            "selected",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["selected_subagent_type"],
            "cheap_review",
        )
        self.assertEqual(
            [event["event_type"] for event in host.events],
            [
                "runtime_control_subagent_resolved",
                "runtime_control_subagent_protocol_resolved",
            ],
        )

    def test_execute_patch_cycle_records_protocol_row_after_runtime_resolution(self) -> None:
        host = _HostStub()
        controller = OrchestratorRuntimeSubagentControlProtocolController(
            host,
            subagent_controller=OrchestratorRuntimeSubagentControlController(host),
        )
        context = SimpleNamespace(runtime_control_stage_decisions=[], dynamic_trace=[])
        resolution = SimpleNamespace(
            final_plan=SimpleNamespace(nodes=[SimpleNamespace(node_id="review_strategy_resolver")]),
            base_results={"review_strategy_resolver": "base"},
            final_patch_rows=[],
            final_results={"review_strategy_resolver": "final"},
            rule_variant_id="current",
            final_plan_variant="current",
            candidate_plan_variants=["current"],
            final_patch=None,
            shadow_payload=None,
        )

        resolved = controller.execute_patch_cycle(
            phase_execution_controller=_PhaseExecutionControllerStub(resolution),
            runtime_binding=self._build_binding(context),
            plan=SimpleNamespace(
                intent="review_thesis",
                phase="review",
                plan_id="plan-1",
                strategy="catalog",
                nodes=[SimpleNamespace(node_id="review_strategy_resolver")],
            ),
            dag_id="review_execution",
            base_max_workers=1,
            final_max_workers=1,
            build_invocation_factory=lambda _plan: {},
            patch_builder=lambda _results: None,
            replan_builder=None,
            allow_current_plan_variant=True,
            candidate_plan_variants_builder=None,
        )

        self.assertIs(resolved, resolution)
        self.assertEqual(
            [row["event"] for row in context.runtime_control_stage_decisions],
            [
                "runtime_control_subagent_phase_resolved",
                "runtime_control_subagent_protocol_resolved",
            ],
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["resolution_source"],
            "phase_patch_cycle",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["decision_type"],
            "keep_current",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["final_plan_variant"],
            "current",
        )
        self.assertEqual(
            [event["event_type"] for event in host.events],
            [
                "runtime_control_subagent_phase_resolved",
                "runtime_control_subagent_protocol_resolved",
            ],
        )


if __name__ == "__main__":
    unittest.main()
