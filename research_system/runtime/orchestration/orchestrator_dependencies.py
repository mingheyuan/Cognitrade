from __future__ import annotations

from dataclasses import dataclass

from research_system.adapters.storage.local_memory_adapter import LocalMemoryAdapter
from research_system.agents.collector.service import CollectorService
from research_system.agents.critic.service import CriticService
from research_system.agents.distiller.service import DistillerService
from research_system.agents.reporter.service import ReporterService
from research_system.agents.reviewer.service import ReviewerService
from research_system.agents.retriever.service import RetrieverService
from research_system.agents.screener.service import ScreenerService
from research_system.agents.screen_reviewer.service import ScreenReviewerService
from research_system.runtime.core.gates import GateEvaluator
from research_system.runtime.platform.settings import Settings
from research_system.runtime.platform.skill_runtime import SkillRuntime
from research_system.runtime.planning.planner import RuntimePlanner
from research_system.runtime.proposals.management.proposal_store import ProposalStore
from research_system.runtime.providers.provider_registry import OpenAIProvider, ProviderRegistry
from research_system.runtime.storage.runs.retention import RunRetentionService
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.runtime.supervision.supervisor import RuntimeSupervisor
from research_system.services.memory import MemoryService
from research_system.services.methodologies.evolver.methodology_evolver import MethodologyEvolver
from research_system.services.methodologies.policy.methodology_manifest_service import MethodologyManifestService
from research_system.services.methodologies.valuation.cashflow_resilience_service import CashflowResilienceService
from research_system.services.methodologies.valuation.comps_service import CompsService
from research_system.services.methodologies.valuation.dcf_service import DCFService
from research_system.services.memory.write.methodology_writer import MethodologyMemoryWriter
from research_system.runtime.orchestration.progress_service import ProgressService
from research_system.services.proposals.generator.proposal_generator import ProposalGenerator
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
    collector: CollectorService
    distiller: DistillerService
    critic: CriticService
    reviewer: ReviewerService
    retriever: RetrieverService
    reporter: ReporterService
    screener: ScreenerService
    screen_reviewer: ScreenReviewerService
    comps_service: CompsService
    dcf_service: DCFService
    cashflow_resilience_service: CashflowResilienceService
    proposal_store: ProposalStore
    methodology_evolver: MethodologyEvolver
    proposal_generator: ProposalGenerator
    methodology_manifest_service: MethodologyManifestService
    retrieval_policy_service: RetrievalPolicyService
    memory_service: MemoryService
    methodology_memory_writer: MethodologyMemoryWriter
    gates: GateEvaluator
    retention: RunRetentionService
    planner: RuntimePlanner
    supervisor: RuntimeSupervisor

    def close(self) -> None:
        close = getattr(self.retriever, "close", None)
        if callable(close):
            close()


def build_orchestrator_dependencies(settings: Settings) -> OrchestratorDependencies:
    provider_registry = ProviderRegistry(settings)
    llm_provider = provider_registry.default()
    task_router = TaskRouter(llm_provider, settings=settings)
    progress_service = ProgressService()
    skill_runtime = SkillRuntime(settings)
    yaml_configs = settings.load_yaml_configs()
    memory_service = MemoryService(LocalMemoryAdapter(settings))

    return OrchestratorDependencies(
        run_store=RunStore(settings.runs_root),
        provider_registry=provider_registry,
        llm_provider=llm_provider,
        task_router=task_router,
        progress_service=progress_service,
        skill_runtime=skill_runtime,
        collector=CollectorService(skill_runtime, settings.runs_root),
        distiller=DistillerService(llm_provider, settings, settings.runs_root),
        critic=CriticService(settings.runs_root),
        reviewer=ReviewerService(
            yaml_configs.get("review_policy"),
            settings.runs_root,
            llm_provider=llm_provider,
            settings=settings,
        ),
        retriever=RetrieverService(llm_provider, settings, yaml_configs.get("retrieval_policy"), settings.runs_root),
        reporter=ReporterService(llm_provider, runs_root=settings.runs_root, settings=settings),
        screener=ScreenerService(runs_root=settings.runs_root),
        screen_reviewer=ScreenReviewerService(
            settings.runs_root,
            llm_provider=llm_provider,
            settings=settings,
        ),
        comps_service=CompsService(),
        dcf_service=DCFService(),
        cashflow_resilience_service=CashflowResilienceService(),
        proposal_store=ProposalStore(settings.proposals_root),
        methodology_evolver=MethodologyEvolver(),
        proposal_generator=ProposalGenerator(),
        methodology_manifest_service=MethodologyManifestService(settings),
        retrieval_policy_service=RetrievalPolicyService(settings),
        memory_service=memory_service,
        methodology_memory_writer=MethodologyMemoryWriter(memory_service),
        gates=GateEvaluator(),
        retention=RunRetentionService(settings),
        planner=RuntimePlanner(llm_provider, settings),
        supervisor=RuntimeSupervisor(llm_provider=llm_provider, settings=settings),
    )
