from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.execution.core.canonical_graph_executor import CanonicalGraphExecutor
from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.orchestration import (
    OrchestratorAdminService,
    OrchestratorAssemblySupport,
    OrchestratorCompatSupport,
    OrchestratorQdrantAdminSupport,
    OrchestratorResearchForwardingSupport,
    OrchestratorResearchSupport,
    OrchestratorRunCoordinator,
    OrchestratorRunTerminalSupport,
    OrchestratorRuntimeIoSupport,
    build_orchestrator_dependencies,
)
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.proposals import (
    OrchestratorProposalApprovalPolicy,
    OrchestratorProposalTransitionSupport,
)


class OrchestratorDependenciesTests(unittest.TestCase):
    def test_dependency_builder_contains_only_current_runtime_components(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            dependencies = build_orchestrator_dependencies(settings)

            self.assertIs(dependencies.provider_registry.default(), dependencies.llm_provider)
            self.assertIs(dependencies.methodology_memory_writer.memory_service, dependencies.memory_service)
            self.assertEqual(dependencies.subagent_discovery_store.root, settings.subagent_discovery_root)
            for legacy_name in (
                "collector", "critic", "distiller", "retriever", "reporter",
                "reviewer", "screener", "screen_reviewer", "supervisor",
            ):
                self.assertFalse(hasattr(dependencies, legacy_name), legacy_name)

    def test_orchestrator_wires_canonical_graph_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            dependencies = build_orchestrator_dependencies(settings)
            with ResearchOrchestrator(settings, dependencies=dependencies) as orchestrator:
                self.assertIs(orchestrator.dependencies, dependencies)
                self.assertIsInstance(orchestrator.graph_executor, CanonicalGraphExecutor)
                self.assertIsInstance(orchestrator.execution_support, OrchestratorExecutionSupport)
                self.assertIsInstance(orchestrator.research_support, OrchestratorResearchSupport)
                self.assertIsInstance(orchestrator.admin, OrchestratorAdminService)
                self.assertIsInstance(orchestrator.admin.qdrant_admin, OrchestratorQdrantAdminSupport)
                self.assertIsInstance(orchestrator.run_coordinator, OrchestratorRunCoordinator)
                self.assertIsInstance(orchestrator.run_coordinator.terminal_support, OrchestratorRunTerminalSupport)
                self.assertIsInstance(orchestrator.assembly, OrchestratorAssemblySupport)
                self.assertIsInstance(orchestrator.runtime_io, OrchestratorRuntimeIoSupport)
                self.assertIsInstance(orchestrator.research_forwarding, OrchestratorResearchForwardingSupport)
                self.assertIsInstance(orchestrator.compat, OrchestratorCompatSupport)
                self.assertIsInstance(
                    orchestrator.research_support.proposals.approval_policy,
                    OrchestratorProposalApprovalPolicy,
                )
                self.assertIsInstance(
                    orchestrator.research_support.proposals.transition_support,
                    OrchestratorProposalTransitionSupport,
                )
                for legacy_name in (
                    "action_runner", "plan_executor", "runtime_control", "runtime_orchestration",
                    "collector", "retriever", "supervisor",
                ):
                    self.assertFalse(hasattr(orchestrator, legacy_name), legacy_name)


if __name__ == "__main__":
    unittest.main()
