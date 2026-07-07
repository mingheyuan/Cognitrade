from __future__ import annotations

import unittest

from research_system.runtime.planning import RuntimePlanner
from research_system.runtime.planning.dynamic_plan_support import PlannerDynamicPlanSupport
from research_system.runtime.planning.entry_decision_support import PlannerEntryDecisionSupport
from research_system.runtime.planning.static_plan_support import PlannerStaticPlanSupport


class PlannerRuntimeTests(unittest.TestCase):
    def test_planner_wires_entry_dynamic_and_static_supports(self) -> None:
        planner = RuntimePlanner()

        self.assertIsInstance(planner.entry_decision, PlannerEntryDecisionSupport)
        self.assertIsInstance(planner.dynamic_support, PlannerDynamicPlanSupport)
        self.assertIsInstance(planner.static_support, PlannerStaticPlanSupport)


if __name__ == "__main__":
    unittest.main()
