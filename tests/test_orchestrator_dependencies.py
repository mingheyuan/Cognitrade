from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from research_system.runtime.execution.core.canonical_graph_executor import CanonicalGraphExecutor
from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.orchestration.orchestrator_context import build_runtime_context
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
from research_system.runtime.storage.jsonl import read_json
from research_system.runtime.storage.runs.run_store import RunPaths, RunStore
from research_system.schemas.execution_runtime import ExecutionRunResult, ExecutionRunSnapshot
from research_system.schemas.execution_graph import ExecutionGraph
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import RunStateSnapshot
from research_system.runtime.proposals import (
    OrchestratorProposalApprovalPolicy,
    OrchestratorProposalTransitionSupport,
)


class OrchestratorDependenciesTests(unittest.TestCase):
    def test_legacy_graph_executor_migrates_legacy_turn_state(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                    "openai_api_key": None,
                    "openai_base_url": None,
                }
            )
            request = RequestEnvelope(
                user_query="legacy graph",
                intent="legal_contract_review",
                user_id="user-1",
                tenant_id="tenant-1",
            )
            run_id, paths, state = RunStore(settings.runs_root).create_run(
                request,
                request.intent or "legacy",
            )
            context = build_runtime_context(
                run_id=run_id,
                paths=paths,
                state=state,
                run_timeout_seconds=settings.run_timeout_seconds,
                thread_id="legacy-thread",
            )

            CanonicalGraphExecutor(settings).execute(
                context=context,
                request=request,
                graph=ExecutionGraph(domain_id="legal_contract", objective="legacy graph"),
            )

            canonical_turn = read_json(paths.root / "turn.json")
            self.assertEqual(canonical_turn["thread_id"], "legacy-thread")
            self.assertEqual(canonical_turn["user_id"], "user-1")
            self.assertEqual(canonical_turn["tenant_id"], "tenant-1")
            self.assertEqual(read_json(paths.root / "result.json")["status"], "completed")

    def test_legacy_graph_executor_forwards_to_turn_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request = RequestEnvelope(user_query="legacy graph")
            run_root = root / "runs" / "run-1"
            run_root.mkdir(parents=True)
            paths = RunPaths(run_root)
            graph = ExecutionGraph(domain_id="legal_contract", objective="legacy graph")
            snapshot = ExecutionRunSnapshot(
                run_id="run-1",
                thread_id="thread-1",
                request_id=request.request_id,
                graph=graph,
                status="completed",
            )
            calls: list[dict] = []

            class FakeTurnRunner:
                def execute_task(self, **kwargs):
                    calls.append(kwargs)
                    return SimpleNamespace(
                        status="completed",
                        result=ExecutionRunResult(snapshot=snapshot),
                    )

                def finalize(self, **_kwargs):
                    return None

            fake_agent_os = SimpleNamespace(turn_runner=FakeTurnRunner())
            executor = CanonicalGraphExecutor(Settings.from_env(root), agent_os=fake_agent_os)
            context = SimpleNamespace(
                run_id="run-1",
                paths=paths,
                thread=SimpleNamespace(thread_id="thread-1"),
                state=RunStateSnapshot(
                    run_id="run-1",
                    request_id=request.request_id,
                    intent="legacy graph",
                    status="running",
                ),
            )

            executor.execute(context=context, request=request, graph=graph)

            self.assertEqual(len(calls), 1)
            self.assertIs(calls[0]["graph"], graph)
            self.assertEqual(calls[0]["run_id"], "run-1")
            self.assertEqual(context.execution_graph, graph)
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
