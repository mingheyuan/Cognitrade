from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.execution import (
    OrchestratorActionRunner,
    OrchestratorExecutionControlSupport,
    OrchestratorExecutionLifecycleSupport,
    OrchestratorExecutionResearchFacade,
    OrchestratorExecutionRuntimeSupport,
    OrchestratorExecutionSupport,
    OrchestratorExecutionTraceSupport,
    OrchestratorPlanExecutor,
)
from research_system.runtime.execution.handlers import OrchestratorAgentHandlers
from research_system.runtime.methodology import (
    OrchestratorMethodologyBankHighlightSupport,
    OrchestratorMethodologyBankMetricSupport,
    OrchestratorMethodologyBuilder,
    OrchestratorMethodologyCompareSupport,
    OrchestratorMethodologyEntryFactory,
    OrchestratorMethodologySnapshotCandidateSupport,
    OrchestratorMethodologySnapshotFields,
    OrchestratorMethodologySnapshotGrouping,
    OrchestratorMethodologySymbolSupport,
)
from research_system.runtime.orchestration import (
    OrchestratorAdminService,
    OrchestratorAssemblySupport,
    OrchestratorCompatSupport,
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
    build_orchestrator_dependencies,
)
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.proposals import (
    OrchestratorProposalAdminSupport,
    OrchestratorProposalApprovalPolicy,
    OrchestratorProposalEntryFactory,
    OrchestratorProposalTransitionSupport,
)


class OrchestratorDependenciesTests(unittest.TestCase):
    def test_dependency_builder_wires_shared_runtime_components(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)

            dependencies = build_orchestrator_dependencies(settings)
            try:
                self.assertIs(dependencies.provider_registry.default(), dependencies.llm_provider)
                self.assertIs(dependencies.collector.skill_runtime, dependencies.skill_runtime)
                self.assertIs(dependencies.reporter.llm_provider, dependencies.llm_provider)
                self.assertIs(dependencies.reporter.settings, settings)
                self.assertIs(dependencies.distiller.llm_provider, dependencies.llm_provider)
                self.assertIs(dependencies.distiller.settings, settings)
                self.assertIs(dependencies.retriever.llm_provider, dependencies.llm_provider)
                self.assertIs(dependencies.supervisor.llm_provider, dependencies.llm_provider)
                self.assertIs(dependencies.methodology_memory_writer.memory_service, dependencies.memory_service)
            finally:
                dependencies.close()

    def test_orchestrator_uses_injected_dependencies_without_changing_public_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)

            dependencies = build_orchestrator_dependencies(settings)
            orchestrator = ResearchOrchestrator(settings, dependencies=dependencies)
            try:
                self.assertIs(orchestrator.dependencies, dependencies)
                self.assertIs(orchestrator.run_store, dependencies.run_store)
                self.assertIs(orchestrator.task_router, dependencies.task_router)
                self.assertIs(orchestrator.collector, dependencies.collector)
                self.assertIs(orchestrator.retriever, dependencies.retriever)
                self.assertIs(orchestrator.supervisor, dependencies.supervisor)
                self.assertIsInstance(orchestrator.research_support, OrchestratorResearchSupport)
                self.assertIsInstance(
                    orchestrator.research_support.methodology.builder,
                    OrchestratorMethodologyBuilder,
                )
                self.assertIsInstance(
                    orchestrator.research_support.methodology.entry_factory,
                    OrchestratorMethodologyEntryFactory,
                )
                self.assertIsInstance(
                    orchestrator.research_support.methodology.bank_scorecard_support.metric_support,
                    OrchestratorMethodologyBankMetricSupport,
                )
                self.assertIsInstance(
                    orchestrator.research_support.methodology.bank_scorecard_support.highlight_support,
                    OrchestratorMethodologyBankHighlightSupport,
                )
                self.assertIsInstance(
                    orchestrator.research_support.methodology.compare_support,
                    OrchestratorMethodologyCompareSupport,
                )
                self.assertIsInstance(
                    orchestrator.research_support.methodology.snapshot_support.grouping,
                    OrchestratorMethodologySnapshotGrouping,
                )
                self.assertIsInstance(
                    orchestrator.research_support.methodology.snapshot_support.fields,
                    OrchestratorMethodologySnapshotFields,
                )
                self.assertIsInstance(
                    orchestrator.research_support.methodology.snapshot_support.candidate_support,
                    OrchestratorMethodologySnapshotCandidateSupport,
                )
                self.assertIsInstance(
                    orchestrator.research_support.methodology.symbol_support,
                    OrchestratorMethodologySymbolSupport,
                )
                self.assertIsInstance(orchestrator.execution_support, OrchestratorExecutionSupport)
                self.assertIsInstance(
                    orchestrator.execution_support.research_facade.forwarding,
                    OrchestratorResearchForwardingSupport,
                )
                self.assertIsInstance(orchestrator.admin, OrchestratorAdminService)
                self.assertIs(orchestrator.admin.proposal_store, orchestrator.proposal_store)
                self.assertIs(orchestrator.admin.research_support, orchestrator.research_support)
                self.assertIsInstance(orchestrator.admin.run_admin, OrchestratorRunAdminSupport)
                self.assertIsInstance(orchestrator.admin.proposal_admin, OrchestratorProposalAdminSupport)
                self.assertIsInstance(orchestrator.admin.qdrant_admin, OrchestratorQdrantAdminSupport)
                self.assertIsInstance(orchestrator.subagent_bridge, OrchestratorSubagentBridge)
                self.assertIs(orchestrator.subagent_bridge.host, orchestrator)
                self.assertIsInstance(orchestrator.execution_support.runtime_support, OrchestratorExecutionRuntimeSupport)
                self.assertIsInstance(
                    orchestrator.execution_support.runtime_support.lifecycle_support,
                    OrchestratorExecutionLifecycleSupport,
                )
                self.assertIsInstance(
                    orchestrator.execution_support.runtime_support.control_support,
                    OrchestratorExecutionControlSupport,
                )
                self.assertIsInstance(
                    orchestrator.execution_support.runtime_support.trace_support,
                    OrchestratorExecutionTraceSupport,
                )
                self.assertIsInstance(orchestrator.execution_support.research_facade, OrchestratorExecutionResearchFacade)
                self.assertIsInstance(orchestrator.action_runner, OrchestratorActionRunner)
                self.assertIs(orchestrator.action_runner.support, orchestrator.execution_support)
                self.assertIsInstance(orchestrator.action_runner.handlers, OrchestratorAgentHandlers)
                self.assertIs(orchestrator.action_runner.handlers.support, orchestrator.execution_support)
                self.assertIsInstance(orchestrator.plan_executor, OrchestratorPlanExecutor)
                self.assertIs(orchestrator.plan_executor.host, orchestrator)
                self.assertIsInstance(orchestrator.run_coordinator, OrchestratorRunCoordinator)
                self.assertIs(orchestrator.run_coordinator.host, orchestrator)
                self.assertIsInstance(orchestrator.assembly, OrchestratorAssemblySupport)
                self.assertIsInstance(orchestrator.runtime_io, OrchestratorRuntimeIoSupport)
                self.assertIsInstance(orchestrator.research_forwarding, OrchestratorResearchForwardingSupport)
                self.assertIsInstance(orchestrator.compat, OrchestratorCompatSupport)
                self.assertIsInstance(
                    orchestrator.run_coordinator.startup_support,
                    OrchestratorRunStartupSupport,
                )
                self.assertIsInstance(
                    orchestrator.run_coordinator.terminal_support,
                    OrchestratorRunTerminalSupport,
                )
                self.assertIsInstance(
                    orchestrator.run_coordinator.terminal_support.gate_support,
                    OrchestratorRunGateSupport,
                )
                self.assertIsInstance(
                    orchestrator.run_coordinator.terminal_support.response_support,
                    OrchestratorRunResponseSupport,
                )
                self.assertIsInstance(
                    orchestrator.run_coordinator.terminal_support.success_support,
                    OrchestratorRunSuccessSupport,
                )
                self.assertIsInstance(
                    orchestrator.run_coordinator.terminal_support.failure_support,
                    OrchestratorRunFailureSupport,
                )
                self.assertIsInstance(
                    orchestrator.research_support.proposals.approval_policy,
                    OrchestratorProposalApprovalPolicy,
                )
                self.assertIsInstance(
                    orchestrator.research_support.proposals.transition_support,
                    OrchestratorProposalTransitionSupport,
                )
                self.assertIsInstance(
                    orchestrator.research_support.proposals.evolution_input_support,
                    OrchestratorMethodologyEvolutionInputSupport,
                )
                self.assertIsInstance(
                    orchestrator.research_support.proposals.entry_factory,
                    OrchestratorProposalEntryFactory,
                )
                self.assertEqual(
                    sorted(orchestrator.action_runner.handlers._handlers.keys()),
                    ["collector", "critic", "distiller", "reporter", "retriever", "reviewer", "screen_reviewer", "screener"],
                )
            finally:
                orchestrator.close()


if __name__ == "__main__":
    unittest.main()
