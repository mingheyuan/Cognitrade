from __future__ import annotations

import unittest

from research_system.runtime import orchestration, providers, storage
from research_system.runtime.orchestration.orchestrator import ResearchOrchestrator
from research_system.runtime.orchestration.orchestrator_dependencies import build_orchestrator_dependencies
from research_system.runtime.orchestration.progress_service import ProgressService
from research_system.runtime.providers.openai_provider import OpenAIProvider
from research_system.runtime.providers.provider_registry import ProviderRegistry
from research_system.runtime.storage import runs as storage_runs_package
from research_system.runtime.storage.jsonl import append_jsonl
from research_system.runtime.storage.runs.checkpoint_store import CheckpointStore
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.services import methodologies, routing
from research_system.services.methodologies.dimensions.dimension_normalizer import DimensionNormalizer
from research_system.services.methodologies.evolver.methodology_evolver import MethodologyEvolver
from research_system.services.methodologies.policy.retrieval_policy_service import RetrievalPolicyService
from research_system.services.routing.router import TaskRouter as PackageTaskRouter
from research_system.services.routing.tables.route_table_runtime import build_intent_route_table_store
from research_system.services.routing.stock_target_resolution import extract_stock_symbols
from research_system.services.routing.router.task_router import TaskRouter


class DirectoryPackageExportsRuntimeTests(unittest.TestCase):
    def test_runtime_providers_package_exports(self) -> None:
        self.assertIs(providers.OpenAIProvider, OpenAIProvider)
        self.assertIs(providers.ProviderRegistry, ProviderRegistry)

    def test_runtime_storage_package_exports(self) -> None:
        self.assertIs(storage.RunStore, RunStore)
        self.assertIs(storage.CheckpointStore, CheckpointStore)
        self.assertIs(storage.append_jsonl, append_jsonl)
        self.assertIs(storage_runs_package.RunStore, RunStore)
        self.assertIs(storage_runs_package.CheckpointStore, CheckpointStore)

    def test_runtime_orchestration_package_exports(self) -> None:
        self.assertIs(orchestration.ResearchOrchestrator, ResearchOrchestrator)
        self.assertIs(orchestration.ProgressService, ProgressService)
        self.assertIs(orchestration.build_orchestrator_dependencies, build_orchestrator_dependencies)

    def test_services_methodologies_package_exports(self) -> None:
        self.assertIs(methodologies.MethodologyEvolver, MethodologyEvolver)
        self.assertIs(methodologies.DimensionNormalizer, DimensionNormalizer)
        self.assertIs(methodologies.RetrievalPolicyService, RetrievalPolicyService)

    def test_services_routing_package_exports(self) -> None:
        self.assertIs(routing.TaskRouter, TaskRouter)
        self.assertIs(PackageTaskRouter, TaskRouter)
        self.assertIs(routing.build_intent_route_table_store, build_intent_route_table_store)
        self.assertIs(routing.extract_stock_symbols, extract_stock_symbols)


if __name__ == "__main__":
    unittest.main()
