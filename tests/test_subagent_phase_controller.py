from __future__ import annotations

import unittest

from research_system.agents._subagent_phase_controller import SubagentPhaseController
from research_system.schemas.subagents import SubagentExecutionRecord, SubagentPlan
from research_system.subagents.base import SubagentCatalogMetadata, SubagentRuntimePolicy
from research_system.subagents.registry import SubagentRegistry


class _CheapReviewSubagent:
    subagent_type = "cheap_review"
    input_model = None
    output_model = None
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="cheap review",
        capabilities=("review_strategy",),
        stage="review",
        cost_class="low",
        latency_class="low",
        suitable_intents=("review_thesis",),
    )

    def run(self, **kwargs):
        return kwargs


class _PreferredReviewSubagent:
    subagent_type = "preferred_review"
    input_model = None
    output_model = None
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="preferred review",
        capabilities=("review_strategy",),
        stage="review",
        cost_class="medium",
        latency_class="medium",
        suitable_intents=("review_thesis",),
    )

    def run(self, **kwargs):
        return kwargs


class SubagentPhaseControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SubagentRegistry([_CheapReviewSubagent(), _PreferredReviewSubagent()])
        self.invocations = []

        def _invoke(invocation):
            self.invocations.append(invocation)
            return (
                {"ok": True, "value": invocation.inputs.get("value")},
                SubagentExecutionRecord(
                    subagent_type=invocation.subagent_type,
                    invocation_id=invocation.invocation_id,
                    status="completed",
                    objective=invocation.objective,
                    inputs=invocation.inputs,
                    output_keys=["ok", "value"],
                ),
            )

        self.controller = SubagentPhaseController(
            registry=self.registry,
            invoke_subagent=_invoke,
            emit_event=None,
            cache_context_builder=lambda **_: None,
            start_from_node_id_builder=lambda **_: None,
        )

    def test_select_catalog_entry_prefers_explicit_type(self) -> None:
        selected = self.controller.select_catalog_entry(
            stage="review",
            intent="review_thesis",
            capability="review_strategy",
            preferred_subagent_type="preferred_review",
        )

        self.assertEqual(selected.subagent_type, "preferred_review")

    def test_execute_plan_injects_plan_metadata_and_trace(self) -> None:
        plan = SubagentPlan(
            intent="review_thesis",
            phase="review",
            strategy="catalog",
            nodes=[
                self.controller.build_catalog_plan_node(
                    node_id="review_strategy_resolver",
                    subagent_type="cheap_review",
                    objective="Resolve review strategy.",
                    inputs={"value": 7},
                    cache_key="review_strategy_resolver",
                )
            ],
        )
        runtime_nodes = {
            "review_strategy_resolver": {
                "builder": lambda _results: self.controller.build_invocation(
                    subagent_type="cheap_review",
                    objective="Resolve review strategy.",
                    inputs={"value": 7},
                    task_name="review:strategy:resolver",
                    agent_path="reviewer/strategy/resolver",
                    phase="review",
                ),
                "metadata": self.controller.build_metadata(
                    task_name="review:strategy:resolver",
                    agent_path="reviewer/strategy/resolver",
                    objective="Resolve review strategy.",
                    phase="review",
                ),
            }
        }

        results = self.controller.execute_plan(
            plan=plan,
            build_invocation=runtime_nodes,
            dag_id="review_execution",
            max_workers=1,
        )

        self.assertIn("review_strategy_resolver", results)
        self.assertEqual(results["review_strategy_resolver"].outputs["value"], 7)
        trace = self.controller.trace_from_results(results=results)
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0]["plan_node_id"], "review_strategy_resolver")
        self.assertEqual(trace[0]["subagent_plan_strategy"], "catalog")
        self.assertEqual(trace[0]["task_name"], "review:strategy:resolver")
        self.assertEqual(trace[0]["agent_path"], "reviewer/strategy/resolver")


if __name__ == "__main__":
    unittest.main()
