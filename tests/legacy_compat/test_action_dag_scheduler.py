from __future__ import annotations

import unittest

from research_system.runtime.execution.core.dag_scheduler import ActionDagScheduler
from research_system.schemas.planning import ActionPlan, ActionSpec


class ActionDagSchedulerTests(unittest.TestCase):
    def test_validate_rejects_missing_dependency_and_cycle(self) -> None:
        scheduler = ActionDagScheduler()

        missing = scheduler.validate(
            ActionPlan(
                intent="stock_check",
                actions=[
                    ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["collect"]),
                ],
            )
        )
        cycle = scheduler.validate(
            ActionPlan(
                intent="stock_check",
                actions=[
                    ActionSpec(node_id="collect", phase="collect", agent="collector", depends_on=["report"]),
                    ActionSpec(node_id="report", phase="report", agent="reporter", depends_on=["collect"]),
                ],
            )
        )

        self.assertFalse(missing.valid)
        self.assertIn("missing_dependency:retrieve->collect", missing.errors)
        self.assertFalse(cycle.valid)
        self.assertTrue(any(error.startswith("cycle:") for error in cycle.errors))

    def test_ready_actions_respect_dependencies_and_skipped_nodes(self) -> None:
        scheduler = ActionDagScheduler()
        pending = [
            ActionSpec(node_id="collect", phase="collect", agent="collector"),
            ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["collect"]),
            ActionSpec(node_id="report", phase="report", agent="reporter", depends_on=["retrieve"]),
        ]

        ready_before = scheduler.ready_actions(
            pending_actions=pending,
            completed_node_ids=[],
            skipped_node_ids=[],
        )
        ready_after_collect = scheduler.ready_actions(
            pending_actions=pending[1:],
            completed_node_ids=["collect"],
            skipped_node_ids=[],
        )
        ready_after_skipped_retrieve = scheduler.ready_actions(
            pending_actions=[pending[2]],
            completed_node_ids=["collect"],
            skipped_node_ids=["retrieve"],
        )

        self.assertEqual([action.node_id for action in ready_before], ["collect"])
        self.assertEqual([action.node_id for action in ready_after_collect], ["retrieve"])
        self.assertEqual([action.node_id for action in ready_after_skipped_retrieve], ["report"])


if __name__ == "__main__":
    unittest.main()
