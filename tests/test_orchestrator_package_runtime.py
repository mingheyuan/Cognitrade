from __future__ import annotations

import unittest

from research_system.runtime.orchestration.orchestrator import (
    OrchestratorError as PackageOrchestratorError,
)
from research_system.runtime.orchestration.orchestrator import (
    ResearchOrchestrator as PackageResearchOrchestrator,
)
from research_system.runtime.orchestration.orchestrator import (
    RunCancelledError as PackageRunCancelledError,
)
from research_system.runtime.orchestration.orchestrator import (
    RunTimedOutError as PackageRunTimedOutError,
)
from research_system.runtime.orchestrator import (
    OrchestratorError,
    ResearchOrchestrator,
    RunCancelledError,
    RunTimedOutError,
)


class OrchestratorPackageRuntimeTests(unittest.TestCase):
    def test_top_level_orchestrator_exports_match_orchestration_package(self) -> None:
        self.assertIs(ResearchOrchestrator, PackageResearchOrchestrator)
        self.assertIs(OrchestratorError, PackageOrchestratorError)
        self.assertIs(RunCancelledError, PackageRunCancelledError)
        self.assertIs(RunTimedOutError, PackageRunTimedOutError)


if __name__ == "__main__":
    unittest.main()
