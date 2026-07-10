from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from research_system.adapters.storage.local_memory_adapter import LocalMemoryAdapter
from research_system.runtime.core.gates import GateEvaluator
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.orchestration.subagent_discovery_store import SubagentDiscoveryStore
from research_system.runtime.platform.settings import Settings
from research_system.runtime.platform.skill_runtime import SkillRuntime
from research_system.runtime.planning.planner import RuntimePlanner
from research_system.runtime.proposals.management.proposal_store import ProposalStore
from research_system.runtime.providers.provider_registry import OpenAIProvider, ProviderRegistry
from research_system.runtime.storage.runs.retention import RunRetentionService
from research_system.runtime.storage.runs.run_store import RunStore
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


class LazyLegacyService:
    """Construct a legacy agent service only when the legacy path accesses it."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._instance: Any | None = None
        self._explicit_override = False

    def _resolve(self) -> Any:
        if self._instance is None:
            self._instance = self._factory()
        return self._instance

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        object.__setattr__(self, "_explicit_override", True)
        setattr(self._resolve(), name, value)

    @property
    def initialized(self) -> bool:
        return self._instance is not None

    @property
    def explicit_override(self) -> bool:
        return self._explicit_override

    def close_if_initialized(self) -> None:
        if self._instance is None:
            return
        close = getattr(self._instance, "close", None)
        if callable(close):
            close()


@dataclass
class OrchestratorDependencies:
    run_store: RunStore
    provider_registry: ProviderRegistry
    llm_provider: OpenAIProvider
    task_router: TaskRouter
    progress_service: ProgressService
    skill_runtime: SkillRuntime
    collector: Any
    distiller: Any
    critic: Any
    reviewer: Any
    retriever: Any
    reporter: Any
    screener: Any
    screen_reviewer: Any
    comps_service: CompsService
    dcf_service: DCFService
    cashflow_resilience_service: CashflowResilienceService
    proposal_store: ProposalStore
    subagent_discovery_store: SubagentDiscoveryStore
    methodology_evolver: MethodologyEvolver
    proposal_generator: ProposalGenerator
    methodology_manifest_service: MethodologyManifestService
    retrieval_policy_service: RetrievalPolicyService
    memory_service: MemoryService
    methodology_memory_writer: MethodologyMemoryWriter
    gates: GateEvaluator
    retention: RunRetentionService
    planner: RuntimePlanner
    supervisor: Any

    def close(self) -> None:
        close_if_initialized = getattr(self.retriever, "close_if_initialized", None)
        if callable(close_if_initialized):
            close_if_initialized()
        else:
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
        collector=LazyLegacyService(
            lambda: _construct_legacy_service("collector",
                skill_runtime,
                settings.runs_root,
                llm_provider=llm_provider,
                settings=settings,
            )
        ),
        distiller=LazyLegacyService(
            lambda: _construct_legacy_service("distiller",
                llm_provider,
                settings,
                settings.runs_root,
                skill_runtime=skill_runtime,
            )
        ),
        critic=LazyLegacyService(
            lambda: _construct_legacy_service("critic",
                settings.runs_root,
                llm_provider=llm_provider,
                settings=settings,
                skill_runtime=skill_runtime,
            )
        ),
        reviewer=LazyLegacyService(
            lambda: _construct_legacy_service("reviewer",
                yaml_configs.get("review_policy"),
                settings.runs_root,
                llm_provider=llm_provider,
                settings=settings,
                skill_runtime=skill_runtime,
            )
        ),
        retriever=LazyLegacyService(
            lambda: _construct_legacy_service("retriever",
                llm_provider,
                settings,
                yaml_configs.get("retrieval_policy"),
                settings.runs_root,
                skill_runtime=skill_runtime,
            )
        ),
        reporter=LazyLegacyService(
            lambda: _construct_legacy_service("reporter",
                llm_provider,
                runs_root=settings.runs_root,
                settings=settings,
                skill_runtime=skill_runtime,
            )
        ),
        screener=LazyLegacyService(
            lambda: _construct_legacy_service("screener",
                runs_root=settings.runs_root,
                llm_provider=llm_provider,
                settings=settings,
                skill_runtime=skill_runtime,
            )
        ),
        screen_reviewer=LazyLegacyService(
            lambda: _construct_legacy_service("screen_reviewer",
                settings.runs_root,
                llm_provider=llm_provider,
                settings=settings,
                skill_runtime=skill_runtime,
            )
        ),
        comps_service=CompsService(),
        dcf_service=DCFService(),
        cashflow_resilience_service=CashflowResilienceService(),
        proposal_store=ProposalStore(settings.proposals_root),
        subagent_discovery_store=SubagentDiscoveryStore(settings.subagent_discovery_root),
        methodology_evolver=MethodologyEvolver(),
        proposal_generator=ProposalGenerator(),
        methodology_manifest_service=MethodologyManifestService(settings),
        retrieval_policy_service=RetrievalPolicyService(settings),
        memory_service=memory_service,
        methodology_memory_writer=MethodologyMemoryWriter(memory_service),
        gates=GateEvaluator(),
        retention=RunRetentionService(settings),
        planner=RuntimePlanner(llm_provider, settings),
        supervisor=(
            None
            if DomainPackRegistry.load(settings.domain_packs_root).list()
            else _construct_legacy_service("supervisor", llm_provider=llm_provider, settings=settings)
        ),
    )


def _construct_legacy_service(name: str, *args: Any, **kwargs: Any) -> Any:
    if name == "collector":
        from research_system.agents.collector.service import CollectorService
        return CollectorService(*args, **kwargs)
    if name == "distiller":
        from research_system.agents.distiller.service import DistillerService
        return DistillerService(*args, **kwargs)
    if name == "critic":
        from research_system.agents.critic.service import CriticService
        return CriticService(*args, **kwargs)
    if name == "reviewer":
        from research_system.agents.reviewer.service import ReviewerService
        return ReviewerService(*args, **kwargs)
    if name == "retriever":
        from research_system.agents.retriever.service import RetrieverService
        return RetrieverService(*args, **kwargs)
    if name == "reporter":
        from research_system.agents.reporter.service import ReporterService
        return ReporterService(*args, **kwargs)
    if name == "screener":
        from research_system.agents.screener.service import ScreenerService
        return ScreenerService(*args, **kwargs)
    if name == "screen_reviewer":
        from research_system.agents.screen_reviewer.service import ScreenReviewerService
        return ScreenReviewerService(*args, **kwargs)
    if name == "supervisor":
        from research_system.runtime.supervision.supervisor import RuntimeSupervisor
        return RuntimeSupervisor(*args, **kwargs)
    raise ValueError(f"unknown legacy service: {name}")
