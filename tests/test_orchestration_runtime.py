from __future__ import annotations

import unittest

from research_system.runtime import orchestration
from research_system.runtime.orchestration.orchestrator_admin import (
    OrchestratorAdminService as OrchestrationAdminService,
)
from research_system.runtime.orchestration.orchestrator_assembly_support import (
    OrchestratorAssemblySupport as OrchestrationAssemblySupport,
)
from research_system.runtime.orchestration.orchestrator_compat_support import (
    OrchestratorCompatSupport as OrchestrationCompatSupport,
)
from research_system.runtime.orchestration.orchestrator_context import (
    RuntimeContext as OrchestrationRuntimeContext,
)
from research_system.runtime.orchestration.orchestrator_dependencies import (
    OrchestratorDependencies as OrchestrationDependencies,
)
from research_system.runtime.orchestration.orchestrator_research_forwarding_support import (
    OrchestratorResearchForwardingSupport as OrchestrationResearchForwardingSupport,
)
from research_system.runtime.orchestration.orchestrator_research_debug import (
    OrchestratorDebugSupport as OrchestrationDebugSupport,
)
from research_system.runtime.orchestration.orchestrator_research_support import (
    OrchestratorResearchSupport as OrchestrationResearchSupport,
)
from research_system.runtime.orchestration.orchestrator_methodology_evolution_input_support import (
    OrchestratorMethodologyEvolutionInputSupport as OrchestrationMethodologyEvolutionInputSupport,
)
from research_system.runtime.orchestration.orchestrator_qdrant_admin_support import (
    OrchestratorQdrantAdminSupport as OrchestrationQdrantAdminSupport,
)
from research_system.runtime.orchestration.orchestrator_run_admin_support import (
    OrchestratorRunAdminSupport as OrchestrationRunAdminSupport,
)
from research_system.runtime.orchestration.orchestrator_run_coordinator import (
    OrchestratorRunCoordinator as OrchestrationRunCoordinator,
)
from research_system.runtime.orchestration.orchestrator_run_failure_support import (
    OrchestratorRunFailureSupport as OrchestrationRunFailureSupport,
)
from research_system.runtime.orchestration.orchestrator_run_gate_support import (
    OrchestratorRunGateSupport as OrchestrationRunGateSupport,
)
from research_system.runtime.orchestration.orchestrator_run_response_support import (
    OrchestratorRunResponseSupport as OrchestrationRunResponseSupport,
)
from research_system.runtime.orchestration.orchestrator_run_startup_support import (
    OrchestratorRunStartupSupport as OrchestrationRunStartupSupport,
)
from research_system.runtime.orchestration.orchestrator_run_success_support import (
    OrchestratorRunSuccessSupport as OrchestrationRunSuccessSupport,
)
from research_system.runtime.orchestration.orchestrator_run_terminal_support import (
    OrchestratorRunTerminalSupport as OrchestrationRunTerminalSupport,
)
from research_system.runtime.orchestration.progress_service import (
    ProgressService as OrchestrationProgressService,
)
from research_system.runtime.orchestration.orchestrator_runtime_io_support import (
    OrchestratorRuntimeIoSupport as OrchestrationRuntimeIoSupport,
)
from research_system.runtime.orchestration.orchestrator_subagent_bridge import (
    OrchestratorSubagentBridge as OrchestrationSubagentBridge,
)
from research_system.runtime.orchestration import (
    OrchestratorAdminService,
    OrchestratorAssemblySupport,
    OrchestratorCompatSupport,
    OrchestratorDependencies,
    OrchestratorMethodologyEvolutionInputSupport,
    OrchestratorQdrantAdminSupport,
    OrchestratorResearchForwardingSupport,
    OrchestratorResearchSupport,
    OrchestratorRunAdminSupport,
    OrchestratorRunCoordinator,
    OrchestratorRunFailureSupport,
    OrchestratorRunGateSupport,
    OrchestratorRunResponseSupport,
    OrchestratorRunStartupSupport,
    OrchestratorRunSuccessSupport,
    OrchestratorRunTerminalSupport,
    OrchestratorRuntimeIoSupport,
    OrchestratorSubagentBridge,
    RuntimeContext,
)
from research_system.runtime.orchestration.orchestrator_research_debug import OrchestratorDebugSupport


class OrchestrationRuntimeTests(unittest.TestCase):
    def test_top_level_orchestration_exports_remain_compatible_with_orchestration_package(self) -> None:
        self.assertIs(RuntimeContext, OrchestrationRuntimeContext)
        self.assertIs(OrchestratorDependencies, OrchestrationDependencies)
        self.assertIs(OrchestratorAssemblySupport, OrchestrationAssemblySupport)
        self.assertIs(OrchestratorAdminService, OrchestrationAdminService)
        self.assertIs(OrchestratorRunCoordinator, OrchestrationRunCoordinator)
        self.assertIs(OrchestratorRunAdminSupport, OrchestrationRunAdminSupport)
        self.assertIs(OrchestratorRunFailureSupport, OrchestrationRunFailureSupport)
        self.assertIs(OrchestratorRunGateSupport, OrchestrationRunGateSupport)
        self.assertIs(OrchestratorRunResponseSupport, OrchestrationRunResponseSupport)
        self.assertIs(OrchestratorRunStartupSupport, OrchestrationRunStartupSupport)
        self.assertIs(OrchestratorRunSuccessSupport, OrchestrationRunSuccessSupport)
        self.assertIs(OrchestratorRunTerminalSupport, OrchestrationRunTerminalSupport)
        self.assertIs(OrchestratorRuntimeIoSupport, OrchestrationRuntimeIoSupport)
        self.assertIs(OrchestratorCompatSupport, OrchestrationCompatSupport)
        self.assertIs(OrchestratorDebugSupport, OrchestrationDebugSupport)
        self.assertIs(orchestration.ProgressService, OrchestrationProgressService)
        self.assertIs(OrchestratorResearchSupport, OrchestrationResearchSupport)
        self.assertIs(OrchestratorResearchForwardingSupport, OrchestrationResearchForwardingSupport)
        self.assertIs(
            OrchestratorMethodologyEvolutionInputSupport,
            OrchestrationMethodologyEvolutionInputSupport,
        )
        self.assertIs(OrchestratorQdrantAdminSupport, OrchestrationQdrantAdminSupport)
        self.assertIs(OrchestratorSubagentBridge, OrchestrationSubagentBridge)


if __name__ == "__main__":
    unittest.main()
