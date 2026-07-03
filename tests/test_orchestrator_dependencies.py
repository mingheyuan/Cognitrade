from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.orchestrator_action_runner import OrchestratorActionRunner
from research_system.runtime.orchestrator_agent_handlers import OrchestratorAgentHandlers
from research_system.runtime.orchestrator_admin import OrchestratorAdminService
from research_system.runtime.orchestrator_assembly_support import (
    OrchestratorAssemblySupport,
)
from research_system.runtime.orchestrator_compat_support import (
    OrchestratorCompatSupport,
)
from research_system.runtime.orchestrator_dependencies import build_orchestrator_dependencies
from research_system.runtime.orchestrator_execution_research_facade import (
    OrchestratorExecutionResearchFacade,
)
from research_system.runtime.orchestrator_execution_control_support import (
    OrchestratorExecutionControlSupport,
)
from research_system.runtime.orchestrator_execution_lifecycle_support import (
    OrchestratorExecutionLifecycleSupport,
)
from research_system.runtime.orchestrator_execution_runtime_support import (
    OrchestratorExecutionRuntimeSupport,
)
from research_system.runtime.orchestrator_execution_support import OrchestratorExecutionSupport
from research_system.runtime.orchestrator_execution_trace_support import (
    OrchestratorExecutionTraceSupport,
)
from research_system.runtime.orchestrator_methodology_builder import (
    OrchestratorMethodologyBuilder,
)
from research_system.runtime.orchestrator_methodology_bank_highlight_support import (
    OrchestratorMethodologyBankHighlightSupport,
)
from research_system.runtime.orchestrator_methodology_bank_metric_support import (
    OrchestratorMethodologyBankMetricSupport,
)
from research_system.runtime.orchestrator_methodology_compare_support import (
    OrchestratorMethodologyCompareSupport,
)
from research_system.runtime.orchestrator_methodology_evolution_input_support import (
    OrchestratorMethodologyEvolutionInputSupport,
)
from research_system.runtime.orchestrator_methodology_snapshot_candidate_support import (
    OrchestratorMethodologySnapshotCandidateSupport,
)
from research_system.runtime.orchestrator_methodology_snapshot_fields import (
    OrchestratorMethodologySnapshotFields,
)
from research_system.runtime.orchestrator_methodology_snapshot_grouping import (
    OrchestratorMethodologySnapshotGrouping,
)
from research_system.runtime.orchestrator_plan_executor import OrchestratorPlanExecutor
from research_system.runtime.orchestrator_run_coordinator import (
    OrchestratorRunCoordinator,
)
from research_system.runtime.orchestrator_run_startup_support import (
    OrchestratorRunStartupSupport,
)
from research_system.runtime.orchestrator_run_failure_support import (
    OrchestratorRunFailureSupport,
)
from research_system.runtime.orchestrator_run_gate_support import (
    OrchestratorRunGateSupport,
)
from research_system.runtime.orchestrator_run_response_support import (
    OrchestratorRunResponseSupport,
)
from research_system.runtime.orchestrator_run_success_support import (
    OrchestratorRunSuccessSupport,
)
from research_system.runtime.orchestrator_run_terminal_support import (
    OrchestratorRunTerminalSupport,
)
from research_system.runtime.orchestrator_runtime_io_support import (
    OrchestratorRuntimeIoSupport,
)
from research_system.runtime.orchestrator_methodology_entry_factory import (
    OrchestratorMethodologyEntryFactory,
)
from research_system.runtime.orchestrator_methodology_symbol_support import (
    OrchestratorMethodologySymbolSupport,
)
from research_system.runtime.orchestrator_proposal_entry_factory import (
    OrchestratorProposalEntryFactory,
)
from research_system.runtime.orchestrator_proposal_admin_support import (
    OrchestratorProposalAdminSupport,
)
from research_system.runtime.orchestrator_proposal_approval_policy import (
    OrchestratorProposalApprovalPolicy,
)
from research_system.runtime.orchestrator_proposal_transition_support import (
    OrchestratorProposalTransitionSupport,
)
from research_system.runtime.orchestrator_qdrant_admin_support import (
    OrchestratorQdrantAdminSupport,
)
from research_system.runtime.orchestrator_research_support import OrchestratorResearchSupport
from research_system.runtime.orchestrator_research_forwarding_support import (
    OrchestratorResearchForwardingSupport,
)
from research_system.runtime.orchestrator_run_admin_support import (
    OrchestratorRunAdminSupport,
)
from research_system.runtime.orchestrator_subagent_bridge import OrchestratorSubagentBridge
from research_system.runtime.settings import Settings, ensure_workspace_layout


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
                self.assertIs(dependencies.distiller.llm_provider, dependencies.llm_provider)
                self.assertIs(dependencies.retriever.llm_provider, dependencies.llm_provider)
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
