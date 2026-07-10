from __future__ import annotations

import unittest

from research_system.runtime.orchestration.orchestrator_run_failure_support import (
    OrchestratorRunFailureSupport,
)
from research_system.runtime.orchestration.orchestrator_run_response_support import (
    OrchestratorRunResponseSupport,
)
from research_system.runtime.orchestration.orchestrator_run_success_support import (
    OrchestratorRunSuccessSupport,
)
from research_system.runtime.orchestration.orchestrator_run_terminal_support import (
    OrchestratorRunTerminalSupport,
)


class OrchestratorRunTerminalRuntimeTests(unittest.TestCase):
    def test_terminal_support_exposes_canonical_response_support(self) -> None:
        terminal = OrchestratorRunTerminalSupport(object())

        self.assertFalse(hasattr(terminal, "gate_support"))
        self.assertIsInstance(terminal.response_support, OrchestratorRunResponseSupport)
        self.assertIsInstance(terminal.success_support, OrchestratorRunSuccessSupport)
        self.assertIsInstance(terminal.failure_support, OrchestratorRunFailureSupport)


if __name__ == "__main__":
    unittest.main()
