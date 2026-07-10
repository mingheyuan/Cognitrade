from __future__ import annotations

import unittest

from research_system.runtime.supervision.action_plan_patch import apply_action_plan_patch
from research_system.schemas.planning import (
    ActionPlan,
    ActionPlanPatch,
    ActionPlanPatchOperation,
    ActionSpec,
)


def _action(*, node_id: str, phase: str = "collect", agent: str = "collector", depends_on: list[str] | None = None) -> ActionSpec:
    return ActionSpec(
        node_id=node_id,
        phase=phase,
        agent=agent,
        depends_on=list(depends_on or []),
    )


class ActionPlanPatchTests(unittest.TestCase):
    def test_insert_and_rewire_patch_updates_plan_and_dispatch_trace(self) -> None:
        plan = ActionPlan(
            intent="stock_screen",
            strategy="dynamic",
            actions=[
                _action(node_id="collect"),
                _action(node_id="screen", phase="screen", agent="screener", depends_on=["collect"]),
                _action(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["screen"]),
                _action(node_id="report", phase="report", agent="reporter", depends_on=["retrieve"]),
            ],
            dispatch_trace={"source": "route_table"},
        )
        patch = ActionPlanPatch(
            operations=[
                ActionPlanPatchOperation(
                    op_type="insert_action",
                    action=_action(
                        node_id="screen_enrich",
                        phase="screen",
                        agent="screener",
                        depends_on=["screen"],
                    ),
                    reason="insert enrich",
                ),
                ActionPlanPatchOperation(
                    op_type="rewire_dependency",
                    target_node_id="retrieve",
                    new_depends_on=["screen_enrich"],
                    remove_depends_on=["screen"],
                    reason="route retrieval through enrich",
                ),
            ],
            notes=["insert_screen_enrich_before_retrieval"],
        )

        updated_plan = apply_action_plan_patch(plan, patch)

        self.assertEqual(
            [action.node_id for action in updated_plan.actions],
            ["collect", "screen", "retrieve", "report", "screen_enrich"],
        )
        retrieve_action = next(action for action in updated_plan.actions if action.node_id == "retrieve")
        self.assertEqual(retrieve_action.depends_on, ["screen_enrich"])
        self.assertEqual(updated_plan.dispatch_trace["applied_patch_ids"], [patch.patch_id])
        self.assertIn("insert_screen_enrich_before_retrieval", updated_plan.notes)

    def test_replace_subgraph_rewrites_downstream_dependencies(self) -> None:
        plan = ActionPlan(
            intent="review_thesis",
            actions=[
                _action(node_id="collect"),
                _action(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["collect"]),
                _action(node_id="report", phase="report", agent="reporter", depends_on=["retrieve"]),
            ],
        )
        patch = ActionPlanPatch(
            operations=[
                ActionPlanPatchOperation(
                    op_type="replace_subgraph",
                    target_node_id="retrieve",
                    replacement_actions=[
                        _action(node_id="review", phase="review", agent="reviewer", depends_on=["collect"]),
                        _action(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["review"]),
                    ],
                    reason="insert review before retrieve",
                )
            ],
        )

        updated_plan = apply_action_plan_patch(plan, patch)

        self.assertEqual(
            [action.node_id for action in updated_plan.actions],
            ["collect", "review", "retrieve", "report"],
        )
        report_action = next(action for action in updated_plan.actions if action.node_id == "report")
        self.assertEqual(report_action.depends_on, ["retrieve"])

    def test_invalid_patch_raises_for_unknown_dependency(self) -> None:
        plan = ActionPlan(
            intent="stock_check",
            actions=[
                _action(node_id="collect"),
                _action(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["collect"]),
            ],
        )
        patch = ActionPlanPatch(
            operations=[
                ActionPlanPatchOperation(
                    op_type="rewire_dependency",
                    target_node_id="retrieve",
                    new_depends_on=["missing_node"],
                    reason="break graph",
                )
            ],
        )

        with self.assertRaisesRegex(ValueError, "unknown action plan dependency: missing_node"):
            apply_action_plan_patch(plan, patch)


if __name__ == "__main__":
    unittest.main()
