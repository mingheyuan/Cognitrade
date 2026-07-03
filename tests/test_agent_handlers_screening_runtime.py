from __future__ import annotations

import unittest

from research_system.runtime.agent_handlers_screening_context import collect_screening_inputs
from research_system.runtime.agent_handlers_screening_enrichment import run_screening_enrichment
from research_system.runtime.agent_handlers_screening_entries import (
    add_screening_candidates,
    add_screening_scorecard,
)


class AgentHandlersScreeningRuntimeTests(unittest.TestCase):
    def test_screening_support_exports_are_available(self) -> None:
        self.assertTrue(callable(collect_screening_inputs))
        self.assertTrue(callable(run_screening_enrichment))
        self.assertTrue(callable(add_screening_candidates))
        self.assertTrue(callable(add_screening_scorecard))


if __name__ == "__main__":
    unittest.main()
