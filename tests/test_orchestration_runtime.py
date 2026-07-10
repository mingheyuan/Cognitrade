from __future__ import annotations

import unittest

from research_system.runtime import orchestration
from research_system.runtime.orchestration import (
    OrchestratorAdminService,
    OrchestratorAssemblySupport,
    OrchestratorCompatSupport,
    OrchestratorDependencies,
    OrchestratorQdrantAdminSupport,
    OrchestratorResearchForwardingSupport,
    OrchestratorResearchSupport,
    OrchestratorRunAdminSupport,
    OrchestratorRunCoordinator,
    OrchestratorRunFailureSupport,
    OrchestratorRunResponseSupport,
    OrchestratorRunStartupSupport,
    OrchestratorRunSuccessSupport,
    OrchestratorRunTerminalSupport,
    OrchestratorRuntimeIoSupport,
    RuntimeContext,
)


class OrchestrationRuntimeTests(unittest.TestCase):
    def test_canonical_orchestration_exports_are_available(self) -> None:
        expected = {
            "OrchestratorAdminService": OrchestratorAdminService,
            "OrchestratorAssemblySupport": OrchestratorAssemblySupport,
            "OrchestratorCompatSupport": OrchestratorCompatSupport,
            "OrchestratorDependencies": OrchestratorDependencies,
            "OrchestratorQdrantAdminSupport": OrchestratorQdrantAdminSupport,
            "OrchestratorResearchForwardingSupport": OrchestratorResearchForwardingSupport,
            "OrchestratorResearchSupport": OrchestratorResearchSupport,
            "OrchestratorRunAdminSupport": OrchestratorRunAdminSupport,
            "OrchestratorRunCoordinator": OrchestratorRunCoordinator,
            "OrchestratorRunFailureSupport": OrchestratorRunFailureSupport,
            "OrchestratorRunResponseSupport": OrchestratorRunResponseSupport,
            "OrchestratorRunStartupSupport": OrchestratorRunStartupSupport,
            "OrchestratorRunSuccessSupport": OrchestratorRunSuccessSupport,
            "OrchestratorRunTerminalSupport": OrchestratorRunTerminalSupport,
            "OrchestratorRuntimeIoSupport": OrchestratorRuntimeIoSupport,
            "RuntimeContext": RuntimeContext,
        }
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertIs(getattr(orchestration, name), value)

    def test_legacy_orchestration_exports_are_absent(self) -> None:
        for name in (
            "OrchestratorDebugSupport",
            "OrchestratorMethodologyEvolutionInputSupport",
            "OrchestratorRunGateSupport",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(orchestration, name))


if __name__ == "__main__":
    unittest.main()
