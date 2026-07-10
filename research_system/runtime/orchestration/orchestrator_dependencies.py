from __future__ import annotations

from dataclasses import dataclass
from research_system.adapters.storage.local_memory_adapter import LocalMemoryAdapter
from research_system.runtime.orchestration.subagent_discovery_store import SubagentDiscoveryStore
from research_system.runtime.platform.settings import Settings
from research_system.runtime.platform.skill_runtime import SkillRuntime
from research_system.runtime.planning.planner import RuntimePlanner
from research_system.runtime.proposals.management.proposal_store import ProposalStore
from research_system.runtime.providers.provider_registry import OpenAIProvider, ProviderRegistry
from research_system.runtime.storage.runs.retention import RunRetentionService
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.services.memory import MemoryService
from research_system.services.methodologies.policy.methodology_manifest_service import MethodologyManifestService
from research_system.services.memory.write.methodology_writer import MethodologyMemoryWriter
from research_system.runtime.orchestration.progress_service import ProgressService
from research_system.services.methodologies.policy.retrieval_policy_service import RetrievalPolicyService
from research_system.services.routing.router.task_router import TaskRouter


@dataclass
class OrchestratorDependencies:
    run_store: RunStore
    provider_registry: ProviderRegistry
    llm_provider: OpenAIProvider
    task_router: TaskRouter
    progress_service: ProgressService
    skill_runtime: SkillRuntime
    proposal_store: ProposalStore
    subagent_discovery_store: SubagentDiscoveryStore
    methodology_manifest_service: MethodologyManifestService
    retrieval_policy_service: RetrievalPolicyService
    memory_service: MemoryService
    methodology_memory_writer: MethodologyMemoryWriter
    retention: RunRetentionService
    planner: RuntimePlanner

    def close(self) -> None:
        return None


def build_orchestrator_dependencies(settings: Settings) -> OrchestratorDependencies:
    provider_registry = ProviderRegistry(settings)
    llm_provider = provider_registry.default()
    task_router = TaskRouter(llm_provider, settings=settings)
    progress_service = ProgressService()
    skill_runtime = SkillRuntime(settings)
    memory_service = MemoryService(LocalMemoryAdapter(settings))

    return OrchestratorDependencies(
        run_store=RunStore(settings.runs_root),
        provider_registry=provider_registry,
        llm_provider=llm_provider,
        task_router=task_router,
        progress_service=progress_service,
        skill_runtime=skill_runtime,
        proposal_store=ProposalStore(settings.proposals_root),
        subagent_discovery_store=SubagentDiscoveryStore(settings.subagent_discovery_root),
        methodology_manifest_service=MethodologyManifestService(settings),
        retrieval_policy_service=RetrievalPolicyService(settings),
        memory_service=memory_service,
        methodology_memory_writer=MethodologyMemoryWriter(memory_service),
        retention=RunRetentionService(settings),
        planner=RuntimePlanner(llm_provider, settings),
    )
